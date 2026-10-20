import AsyncStorage from '@react-native-async-storage/async-storage';
import NetInfo from '@react-native-community/netinfo';
import { createUserWithEmailAndPassword, onAuthStateChanged, sendPasswordResetEmail, signInWithEmailAndPassword, signOut, updateProfile, type User } from 'firebase/auth';
import { collection, doc, getDoc, getDocs, limit, onSnapshot, orderBy, query, runTransaction, serverTimestamp, setDoc, startAfter, where, type QueryDocumentSnapshot } from 'firebase/firestore';
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';

import { DEFAULT_PREFERENCES, DEMO_MODE, STATION } from '@/config/arribo';
import { DEFAULT_STATION, initialStation, stationAvailable, stationById } from '@/data/stations';
import { ApiError, apiRequest, validatePrediction } from '@/services/api';
import { demoAlerts, demoHistory, demoPrediction } from '@/services/demo';
import { firebaseServices } from '@/services/firebase';
import { planProfileWrite } from '@/services/profile-plan';
import { completeRegistration } from '@/services/registration';
import type { Alert, DemoScenario, HistoryEntry, PendingFeedback, Prediction, Preferences, Session, Station } from '@/types/arribo';
import { displayNameError, normalizedProfileName } from '@/utils/profile-name';
import { createStationSelection, feedbackPayload, resolveStationPrediction, type StationSnapshot } from '@/utils/station-selection';

type AppState = {
  booting: boolean; session: Session | null; online: boolean; mode: 'demo' | 'live';
  history: HistoryEntry[]; alerts: Alert[]; preferences: Preferences; scenario: DemoScenario;
  selectedStation: Station; selectStation: (id: string) => void;
  error: string | null; accountNotice: string | null; dataReady: boolean; hasMore: boolean; pendingCount: number;
  profilePending: boolean; profileSyncing: boolean; retryProfile: (name?: string) => Promise<void>;
  historyLoading: boolean; alertsLoading: boolean; alertsError: string | null; retryAlerts: () => void;
  login: (email: string, password: string) => Promise<void>;
  registerAccount: (name: string, email: string, password: string) => Promise<void>;
  enterDemo: () => Promise<void>; logout: () => Promise<void>; resetPassword: (email: string) => Promise<void>;
  predict: () => Promise<Prediction>; feedback: (queryId: string, minutes: number) => Promise<'sent' | 'queued'>;
  updatePreferences: (changes: Partial<Preferences>) => Promise<void>;
  refreshHistory: (more?: boolean) => Promise<void>; retryPending: () => Promise<void>;
  setScenario: (value: DemoScenario) => void;
};
const Context = createContext<AppState | null>(null);
function userSession(user: User | null): Session | null {
  return user ? { uid: user.uid, email: user.email, displayName: user.displayName, demo: false } : null;
}

function toISO(value: unknown): string {
  if (value && typeof value === 'object' && 'toDate' in value) return (value as { toDate: () => Date }).toDate().toISOString();
  return String(value);
}
function friendlyError(error: unknown) {
  const code = (error as { code?: string })?.code;
  if (code === 'permission-denied') return 'Firestore rechazó el acceso. Comprueba el proyecto y que sus reglas probadas estén publicadas.';
  if (code === 'unavailable') return 'No se pudo conectar con Firestore. Revisa la conexión y la configuración de la base de datos.';
  if (code?.startsWith('auth/')) return ({ 'auth/invalid-credential': 'Correo o contraseña incorrectos.', 'auth/wrong-password': 'Correo o contraseña incorrectos.', 'auth/user-not-found': 'Correo o contraseña incorrectos.', 'auth/email-already-in-use': 'Este correo ya tiene una cuenta. Inicia sesión o recupera tu contraseña.', 'auth/weak-password': 'La contraseña no cumple la política del proyecto.', 'auth/password-does-not-meet-requirements': 'Revisa los requisitos de contraseña indicados.', 'auth/invalid-email': 'Revisa el correo electrónico.', 'auth/network-request-failed': 'Comprueba tu conexión.', 'auth/too-many-requests': 'Demasiados intentos. Espera un momento antes de volver a probar.' } as Record<string, string>)[code] ?? 'No se pudo acceder. Revisa los datos e inténtalo de nuevo.';
  return error instanceof Error ? error.message : 'No se pudo completar la operación.';
}

async function ensureProfile(uid: string, email: string | null, name?: string | null) {
  const { db } = firebaseServices(); const ref = doc(db, 'users', uid);
  // Registration and the auth observer can run concurrently. Never rewrite
  // created_at on an existing profile (rules deliberately make it immutable).
  return runTransaction(db, async transaction => {
    const snapshot = await transaction.get(ref);
    const existing = snapshot.exists() ? snapshot.data() : undefined;
    if (firebaseServices().auth.currentUser?.uid !== uid) throw new Error('La sesión cambió durante la sincronización del perfil.');
    const plan = planProfileWrite(existing, email, name, DEFAULT_PREFERENCES, serverTimestamp());
    if (plan.kind === 'create') transaction.set(ref, plan.data);
    if (plan.kind === 'update') transaction.update(ref, plan.data);
    return normalizedProfileName(existing?.display_name) ?? (plan.kind !== 'none' ? normalizedProfileName(name) : undefined);
  });
}

const pendingNameKey = (uid: string) => `arribo:live:${uid}:pending-profile-name`;

export function ArriboProvider({ children }: { children: ReactNode }) {
  const mode = DEMO_MODE ? 'demo' : 'live';
  const [selection] = useState(() => createStationSelection(DEFAULT_STATION));
  const [selectedStation, setSelectedStation] = useState<Station>(DEFAULT_STATION);
  const [booting, setBooting] = useState(true);
  const [session, setSession] = useState<Session | null>(null);
  const [online, setOnline] = useState(true);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [preferences, setPreferences] = useState<Preferences>(DEFAULT_PREFERENCES);
  const [scenario, setScenario] = useState<DemoScenario>('normal');
  const [error, setError] = useState<string | null>(null);
  const [accountNotice, setAccountNotice] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [alertsLoading, setAlertsLoading] = useState(false);
  const [alertsError, setAlertsError] = useState<string | null>(null);
  const [alertsAttempt, setAlertsAttempt] = useState(0);
  const [loadedUid, setLoadedUid] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [pending, setPending] = useState<PendingFeedback[]>([]);
  const activeUid = useRef<string | null>(null);
  const cursor = useRef<QueryDocumentSnapshot | null>(null);
  const sending = useRef(false);
  const requestId = useRef<(StationSnapshot & { id: string }) | null>(null);
  const queueRef = useRef<PendingFeedback[]>([]);
  const authBusy = useRef(false);
  const registering = useRef(false);
  const pendingNames = useRef(new Map<string, string>());
  const syncingProfile = useRef<string | null>(null);
  const [profileStatus, setProfileStatus] = useState<{ uid: string; status: 'pending' | 'syncing' | 'synced' } | null>(null);
  const [profileName, setProfileName] = useState<{ uid: string; name: string } | null>(null);

  const applyPreferredStation = useCallback((id: string | undefined) => {
    const snapshot = selection.capture();
    const station = selection.applyPreference(initialStation(id, mode));
    if (!selection.matches(snapshot)) { requestId.current = null; setAlerts([]); }
    setSelectedStation(station);
  }, [selection, mode]);
  const selectStation = useCallback((id: string) => {
    const station = stationById(id);
    if (!station || !stationAvailable(station, mode)) throw new Error('Esta estación no está habilitada para consultas en este modo.');
    const snapshot = selection.capture();
    const next = selection.select(station);
    if (!selection.matches(snapshot)) { requestId.current = null; setAlerts([]); setError(null); setAlertsError(null); }
    setSelectedStation(next);
  }, [selection, mode]);

  useEffect(() => NetInfo.addEventListener(state => setOnline(state.isConnected !== false && state.isInternetReachable !== false)), []);
  useEffect(() => {
    let alive = true;
    if (DEMO_MODE) {
      AsyncStorage.getItem('arribo:demo-session').then(value => {
        if (!alive || !value) return;
        const restored = JSON.parse(value) as Session;
        if (restored.uid !== 'local-demo' || restored.demo !== true) throw new Error('Invalid local session');
        setSession(restored);
      }).catch(() => { if (alive) setError('No se pudo restaurar la sesión local. Puedes volver a entrar.'); }).finally(() => { if (alive) setBooting(false); });
      return () => { alive = false; };
    }
    try {
      return onAuthStateChanged(firebaseServices().auth, user => {
        // Firebase signs in before profile steps finish. Keep the form mounted
        // until registration completes so partial success cannot look like failure.
        if (!registering.current) setSession(userSession(user));
        setBooting(false);
      });
    } catch (failure) { Promise.resolve().then(() => { if (alive) { setError(friendlyError(failure)); setBooting(false); } }); }
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    const uid = session?.uid ?? null;
    activeUid.current = uid;
    setSelectedStation(selection.reset());
    // Clear the previous account before hydrating the new account's private data.
    setLoadedUid(null); setHistory([]); setAlerts([]); setPending([]); setPreferences(DEFAULT_PREFERENCES); setHasMore(false); setHistoryLoading(false); setAlertsLoading(false); setAlertsError(null);
    queueRef.current = []; cursor.current = null; requestId.current = null;
    if (!uid) return;
    let alive = true;
    AsyncStorage.getItem(`arribo:${DEMO_MODE ? 'demo' : 'live'}:${uid}`).then(value => {
      if (!alive) return;
      const saved = value ? JSON.parse(value) : null;
      setHistory(saved?.history ?? (session?.demo ? demoHistory() : []));
      setAlerts(saved?.alerts ?? (session?.demo ? demoAlerts() : []));
      const restoredPreferences = { ...DEFAULT_PREFERENCES, ...saved?.preferences };
      setPreferences(restoredPreferences); applyPreferredStation(restoredPreferences.preferred_station);
      // Legacy reports predate multi-station demo. Recover original station
      // from the query when possible; all earlier real requests were rio_consulado.
      queueRef.current = (saved?.pending ?? []).map((report: PendingFeedback) => ({ ...report, station_id: report.station_id ?? saved?.history?.find((entry: HistoryEntry) => entry.query_id === report.query_id)?.station_id ?? STATION.id }));
      setPending([...queueRef.current]);
    }).catch(() => { if (alive) setError('No se pudieron cargar los datos guardados.'); }).finally(() => { if (alive) setLoadedUid(uid); });
    return () => { alive = false; };
  }, [session, selection, applyPreferredStation]);

  useEffect(() => {
    if (session && loadedUid === session.uid) AsyncStorage.setItem(`arribo:${DEMO_MODE ? 'demo' : 'live'}:${session.uid}`, JSON.stringify({ history, alerts, preferences, pending })).catch(() => setError('No se pudieron guardar los datos en el dispositivo.'));
  }, [session, loadedUid, history, alerts, preferences, pending]);

  const refreshHistory = useCallback(async (more = false) => {
    if (!session || session.demo || !online || (more && !cursor.current)) return;
    const uid = session.uid;
    setHistoryLoading(true);
    try {
      const ref = collection(firebaseServices().db, 'query_history', uid, 'queries');
      const constraints = [orderBy('requested_at', 'desc'), ...(more && cursor.current ? [startAfter(cursor.current)] : []), limit(20)];
      const snapshot = await getDocs(query(ref, ...constraints));
      if (activeUid.current !== uid) return;
      const entries = snapshot.docs.map(item => {
        const data = item.data();
        return validatePrediction({ ...data, query_id: item.id, requested_at: toISO(data.requested_at ?? data.timestamp), timestamp: toISO(data.timestamp), expires_at: toISO(data.expires_at) } as HistoryEntry) as HistoryEntry;
      });
      cursor.current = snapshot.docs.at(-1) ?? null; setHasMore(entries.length === 20);
      setHistory(previous => {
        const merged = more ? [...new Map([...previous, ...entries].map(item => [item.query_id, item])).values()] : entries;
        return merged.map(item => {
          const report = queueRef.current.find(queued => queued.query_id === item.query_id);
          return report ? { ...item, actual_min: report.actual_min, feedback_pending: true } : item;
        });
      });
    } finally { if (activeUid.current === uid) setHistoryLoading(false); }
  }, [session, online]);

  useEffect(() => {
    if (!session || session.demo || loadedUid !== session.uid || !online) return;
    const uid = session.uid; const { db } = firebaseServices();
    refreshHistory().catch(failure => setError(friendlyError(failure)));
    getDoc(doc(db, 'users', uid)).then(snapshot => {
      if (activeUid.current !== uid) return;
      if (snapshot.exists()) {
        const storedPreferences = { ...DEFAULT_PREFERENCES, ...snapshot.data().preferences };
        setPreferences(storedPreferences); applyPreferredStation(storedPreferences.preferred_station);
      }
    }).catch(failure => { if (activeUid.current === uid) setError(friendlyError(failure)); });
  }, [session, loadedUid, online, refreshHistory, applyPreferredStation]);

  const retryProfile = useCallback(async (providedName?: string) => {
    if (!session || session.demo) return;
    if (!online) throw new Error('Necesitas conexión para sincronizar tu perfil.');
    if (providedName !== undefined && displayNameError(providedName)) throw new Error('Agrega un nombre válido');
    const uid = session.uid;
    if (activeUid.current !== uid || syncingProfile.current === uid) return;
    syncingProfile.current = uid;
    setProfileStatus({ uid, status: 'syncing' });
    try {
      if (providedName !== undefined) {
        const name = providedName.trim();
        pendingNames.current.set(uid, name);
        await AsyncStorage.setItem(pendingNameKey(uid), name).catch(() => {});
      }
      const saved = pendingNames.current.get(uid) ?? await AsyncStorage.getItem(pendingNameKey(uid));
      if (activeUid.current !== uid) return;
      const candidate = normalizedProfileName(saved) ?? normalizedProfileName(session.displayName);
      const name = await ensureProfile(uid, session.email, candidate);
      if (activeUid.current !== uid) return;
      if (!name) throw new Error('Agrega un nombre válido');
      setProfileName({ uid, name });
      const { auth } = firebaseServices();
      if (auth.currentUser?.uid !== uid) return;
      if (auth.currentUser.displayName !== name) await updateProfile(auth.currentUser, { displayName: name });
      if (activeUid.current !== uid) return;
      await AsyncStorage.removeItem(pendingNameKey(uid));
      pendingNames.current.delete(uid);
      if (activeUid.current !== uid) return;
      setProfileStatus({ uid, status: 'synced' }); setAccountNotice(null);
    } catch (failure) {
      if (activeUid.current === uid) {
        setProfileStatus({ uid, status: 'pending' });
        const message = failure instanceof Error && failure.message === 'Agrega un nombre válido'
          ? 'Agrega un nombre válido' : 'No se pudo sincronizar el perfil. Reintenta.';
        setAccountNotice(message);
        throw new Error(failure instanceof Error && failure.message === 'Agrega un nombre válido' ? message : friendlyError(failure));
      }
    } finally { if (syncingProfile.current === uid) syncingProfile.current = null; }
  }, [session, online]);

  useEffect(() => {
    if (!session || session.demo || loadedUid !== session.uid || !online) return;
    void retryProfile().catch(() => {});
  }, [session, loadedUid, online, retryProfile]);

  useEffect(() => {
    if (!session || loadedUid !== session.uid) return;
    const snapshot = selection.capture(); const uid = session.uid;
    if (session.demo) { setAlerts(demoAlerts(selectedStation.id)); return; }
    if (!online) return;
    const { db } = firebaseServices();
    setAlertsLoading(true); setAlertsError(null);
    return onSnapshot(query(collection(db, 'alerts'), where('station_id', '==', selectedStation.id), where('active', '==', true)), result => {
      if (activeUid.current !== uid || !selection.matches(snapshot)) return;
      setAlerts(result.docs.map(item => {
        const data = item.data();
        return { ...data, id: item.id, created_at: toISO(data.created_at), expires_at: toISO(data.expires_at) } as Alert;
      }));
      setAlertsLoading(false);
    }, failure => { if (activeUid.current === uid && selection.matches(snapshot)) { setAlertsLoading(false); setAlertsError(friendlyError(failure)); } });
  }, [session, loadedUid, online, selectedStation.id, selection, alertsAttempt]);

  const retryPending = useCallback(async () => {
    if (!session || session.demo || !online || loadedUid !== session.uid || sending.current) return;
    sending.current = true; const uid = session.uid;
    try {
      for (const report of [...queueRef.current]) {
        await apiRequest('/feedback', feedbackPayload(report));
        if (activeUid.current !== uid) return;
        queueRef.current = queueRef.current.filter(item => item.query_id !== report.query_id); setPending([...queueRef.current]);
        setHistory(previous => previous.map(item => item.query_id === report.query_id ? { ...item, actual_min: report.actual_min, feedback_pending: false } : item));
      }
    } catch (failure) { if (activeUid.current === uid) setError(friendlyError(failure)); }
    finally { sending.current = false; }
  }, [session, online, loadedUid]);
  useEffect(() => { void retryPending(); }, [retryPending]);

  async function login(email: string, password: string) {
    if (authBusy.current) return;
    authBusy.current = true;
    try {
      const { auth } = firebaseServices();
      await signInWithEmailAndPassword(auth, email.trim(), password);
      setError(null); setAccountNotice(null);
    } catch (failure) { throw new Error(friendlyError(failure)); }
    finally { authBusy.current = false; }
  }
  async function registerAccount(name: string, email: string, password: string) {
    if (authBusy.current) return;
    if (DEMO_MODE) throw new Error('El registro real no está disponible en demostración.');
    const nameError = displayNameError(name);
    if (nameError) throw new Error(nameError);
    const trimmedName = name.trim();
    let recoverySaved = true;
    authBusy.current = true; registering.current = true;
    try {
      const { auth } = firebaseServices();
      const result = await completeRegistration({
        create: async () => {
          const user = (await createUserWithEmailAndPassword(auth, email.trim(), password)).user;
          pendingNames.current.set(user.uid, trimmedName);
          // Only the name is persisted, never passwords or their confirmation.
          // A local storage failure must not turn an existing Auth account into a failed registration.
          await AsyncStorage.setItem(pendingNameKey(user.uid), trimmedName).catch(() => { recoverySaved = false; });
          return user;
        },
        saveName: user => updateProfile(user, { displayName: trimmedName }),
        ensureProfile: async user => { await ensureProfile(user.uid, user.email, trimmedName); },
      });
      setProfileStatus({ uid: result.user.uid, status: result.pending.length ? 'pending' : 'synced' });
      if (!result.pending.length) {
        pendingNames.current.delete(result.user.uid);
        await AsyncStorage.removeItem(pendingNameKey(result.user.uid)).catch(() => {});
      }
      setError(null);
      setAccountNotice(result.pending.length ? `Tu cuenta de acceso ya fue creada. ${result.pending.includes('name') ? 'No se pudo guardar el nombre en Firebase Auth. ' : ''}${result.pending.includes('profile') ? 'El perfil de Firestore sigue pendiente; comprueba su configuración y permisos. ' : ''}${!recoverySaved && result.pending.length === 2 ? 'Tampoco se pudo guardar el nombre para recuperarlo al reiniciar; reintenta desde Configuración antes de cerrar la app. ' : ''}No necesitas volver a registrarte.` : null);
      setSession(userSession(auth.currentUser));
    } catch (failure) { throw new Error(friendlyError(failure)); }
    finally { registering.current = false; authBusy.current = false; }
  }
  async function enterDemo() {
    const user: Session = { uid: 'local-demo', email: null, demo: true };
    await AsyncStorage.setItem('arribo:demo-session', JSON.stringify(user)); setSession(user); setError(null);
  }
  async function logout() {
    activeUid.current = null;
    if (session) await AsyncStorage.setItem(`arribo:${DEMO_MODE ? 'demo' : 'live'}:${session.uid}`, JSON.stringify({ history, alerts, preferences, pending: queueRef.current }));
    if (session?.demo) await AsyncStorage.removeItem('arribo:demo-session'); else await signOut(firebaseServices().auth);
    setSession(null); setError(null); setAccountNotice(null);
  }
  async function predict() {
    if (!session || loadedUid !== session.uid) throw new Error('Espera a que se cargue tu sesión.');
    if (!online && !session.demo) throw new Error('Necesitas conexión para obtener una predicción actual.');
    const uid = session.uid;
    const station = selection.current(); const snapshot = selection.capture();
    if (!stationAvailable(station, session.demo ? 'demo' : 'live')) throw new Error('No hay predicciones reales disponibles para esta estación.');
    const request = requestId.current ?? { ...snapshot, id: `query-${Date.now()}-${Math.random().toString(36).slice(2, 12)}` }; requestId.current = request;
    const result = await resolveStationPrediction(station.id, () => activeUid.current === uid && selection.matches(snapshot), async () => session.demo ? demoPrediction(request.id, scenario, station.id) : validatePrediction(await apiRequest<Prediction>('/predict', { station_id: station.id, request_id: request.id })));
    requestId.current = null; setHistory(previous => [{ ...result, requested_at: result.requested_at ?? new Date().toISOString() }, ...previous.filter(item => item.query_id !== result.query_id)]);
    return result;
  }
  async function feedback(queryId: string, minutes: number): Promise<'sent' | 'queued'> {
    const original = history.find(item => item.query_id === queryId);
    if (!session || !original) throw new Error('No se encontró la consulta.');
    if (!Number.isFinite(minutes) || minutes < 0 || minutes > 180) throw new Error('Ingresa una espera entre 0 y 180 minutos.');
    const uid = session.uid; const report: PendingFeedback = { query_id: queryId, actual_min: minutes, station_id: original.station_id };
    if (!session.demo && online) {
      try {
        await apiRequest('/feedback', feedbackPayload(report));
        if (activeUid.current !== uid) throw new Error('La sesión cambió.');
        setHistory(previous => previous.map(item => item.query_id === queryId ? { ...item, actual_min: minutes, feedback_pending: false } : item)); return 'sent';
      } catch (failure) { if (!(failure instanceof ApiError) || failure.code !== 'NETWORK') throw failure; }
    } else if (session.demo) {
      setHistory(previous => previous.map(item => item.query_id === queryId ? { ...item, actual_min: minutes } : item)); return 'sent';
    }
    if (activeUid.current !== uid) throw new Error('La sesión cambió. Vuelve a iniciar sesión para enviar el reporte.');
    queueRef.current = [...queueRef.current.filter(item => item.query_id !== queryId), report];
    await AsyncStorage.setItem(`arribo:live:${uid}`, JSON.stringify({ history, alerts, preferences, pending: queueRef.current }));
    setPending([...queueRef.current]); setHistory(previous => previous.map(item => item.query_id === queryId ? { ...item, actual_min: minutes, feedback_pending: true } : item));
    return 'queued';
  }
  async function updatePreferences(changes: Partial<Preferences>) {
    if (!session) return;
    const uid = session.uid;
    const next = { ...preferences, ...changes };
    if (!session.demo) await setDoc(doc(firebaseServices().db, 'users', session.uid), { preferences: next }, { merge: true });
    if (activeUid.current === uid) setPreferences(next);
  }
  const visibleSession = session && profileName?.uid === session.uid ? { ...session, displayName: profileName.name } : session;
  return <Context.Provider value={{ booting, session: visibleSession, online, mode, selectedStation, selectStation, history, alerts, preferences, scenario, error, accountNotice, profilePending: profileStatus?.uid === session?.uid && profileStatus?.status === 'pending', profileSyncing: profileStatus?.uid === session?.uid && profileStatus?.status === 'syncing', retryProfile, historyLoading, alertsLoading, alertsError, retryAlerts: () => setAlertsAttempt(value => value + 1), dataReady: !!session && loadedUid === session.uid, hasMore, pendingCount: pending.length, login, registerAccount, enterDemo, logout, resetPassword: async email => { try { await sendPasswordResetEmail(firebaseServices().auth, email.trim()); } catch (failure) { throw new Error(friendlyError(failure)); } }, predict, feedback, updatePreferences, refreshHistory, retryPending, setScenario }}>{children}</Context.Provider>;
}
export function useArribo() {
  const state = useContext(Context); if (!state) throw new Error('ArriboProvider no está disponible.'); return state;
}
