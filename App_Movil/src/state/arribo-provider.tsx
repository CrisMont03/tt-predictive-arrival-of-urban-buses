import AsyncStorage from '@react-native-async-storage/async-storage';
import NetInfo from '@react-native-community/netinfo';
import { createUserWithEmailAndPassword, onAuthStateChanged, sendPasswordResetEmail, signInWithEmailAndPassword, signOut } from 'firebase/auth';
import { collection, doc, getDoc, getDocs, limit, onSnapshot, orderBy, query, runTransaction, serverTimestamp, setDoc, startAfter, where, type QueryDocumentSnapshot } from 'firebase/firestore';
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';

import { DEFAULT_PREFERENCES, DEMO_MODE, STATION } from '@/config/arribo';
import { ApiError, apiRequest, validatePrediction } from '@/services/api';
import { demoAlerts, demoHistory, demoPrediction } from '@/services/demo';
import { firebaseServices } from '@/services/firebase';
import type { Alert, DemoScenario, HistoryEntry, PendingFeedback, Prediction, Preferences, Session } from '@/types/arribo';

type AppState = {
  booting: boolean; session: Session | null; online: boolean; mode: 'demo' | 'live';
  history: HistoryEntry[]; alerts: Alert[]; preferences: Preferences; scenario: DemoScenario;
  error: string | null; dataReady: boolean; hasMore: boolean; pendingCount: number;
  login: (email: string, password: string, register?: boolean) => Promise<void>;
  enterDemo: () => Promise<void>; logout: () => Promise<void>; resetPassword: (email: string) => Promise<void>;
  predict: () => Promise<Prediction>; feedback: (queryId: string, minutes: number) => Promise<'sent' | 'queued'>;
  updatePreferences: (changes: Partial<Preferences>) => Promise<void>;
  refreshHistory: (more?: boolean) => Promise<void>; retryPending: () => Promise<void>;
  setScenario: (value: DemoScenario) => void;
};
const Context = createContext<AppState | null>(null);

function toISO(value: unknown): string {
  if (value && typeof value === 'object' && 'toDate' in value) return (value as { toDate: () => Date }).toDate().toISOString();
  return String(value);
}
function friendlyError(error: unknown) {
  const code = (error as { code?: string })?.code;
  if (code?.startsWith('auth/')) return ({ 'auth/invalid-credential': 'Correo o contraseña incorrectos.', 'auth/email-already-in-use': 'Este correo ya tiene una cuenta.', 'auth/weak-password': 'Usa al menos seis caracteres.', 'auth/invalid-email': 'Revisa el correo electrónico.', 'auth/network-request-failed': 'Comprueba tu conexión.' } as Record<string, string>)[code] ?? 'No se pudo acceder. Revisa los datos e inténtalo de nuevo.';
  return error instanceof Error ? error.message : 'No se pudo completar la operación.';
}

async function ensureProfile(uid: string, email: string | null) {
  const { db } = firebaseServices(); const ref = doc(db, 'users', uid);
  // Registration and the auth observer can run concurrently. Never rewrite
  // created_at on an existing profile (rules deliberately make it immutable).
  await runTransaction(db, async transaction => {
    if (!(await transaction.get(ref)).exists()) transaction.set(ref, { email, created_at: serverTimestamp(), preferences: DEFAULT_PREFERENCES });
  });
}

export function ArriboProvider({ children }: { children: ReactNode }) {
  const [booting, setBooting] = useState(true);
  const [session, setSession] = useState<Session | null>(null);
  const [online, setOnline] = useState(true);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [preferences, setPreferences] = useState<Preferences>(DEFAULT_PREFERENCES);
  const [scenario, setScenario] = useState<DemoScenario>('normal');
  const [error, setError] = useState<string | null>(null);
  const [loadedUid, setLoadedUid] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [pending, setPending] = useState<PendingFeedback[]>([]);
  const activeUid = useRef<string | null>(null);
  const cursor = useRef<QueryDocumentSnapshot | null>(null);
  const sending = useRef(false);
  const requestId = useRef<string | null>(null);
  const queueRef = useRef<PendingFeedback[]>([]);

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
      return onAuthStateChanged(firebaseServices().auth, user => { setSession(user ? { uid: user.uid, email: user.email, demo: false } : null); setBooting(false); });
    } catch (failure) { Promise.resolve().then(() => { if (alive) { setError(friendlyError(failure)); setBooting(false); } }); }
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    const uid = session?.uid ?? null;
    activeUid.current = uid;
    // Clear the previous account before hydrating the new account's private data.
    setLoadedUid(null); setHistory([]); setAlerts([]); setPending([]); setPreferences(DEFAULT_PREFERENCES); setHasMore(false);
    queueRef.current = []; cursor.current = null; requestId.current = null;
    if (!uid) return;
    let alive = true;
    AsyncStorage.getItem(`arribo:${DEMO_MODE ? 'demo' : 'live'}:${uid}`).then(value => {
      if (!alive) return;
      const saved = value ? JSON.parse(value) : null;
      setHistory(saved?.history ?? (session?.demo ? demoHistory() : []));
      setAlerts(saved?.alerts ?? (session?.demo ? demoAlerts() : []));
      setPreferences({ ...DEFAULT_PREFERENCES, ...saved?.preferences });
      queueRef.current = saved?.pending ?? []; setPending([...queueRef.current]);
    }).catch(() => { if (alive) setError('No se pudieron cargar los datos guardados.'); }).finally(() => { if (alive) setLoadedUid(uid); });
    return () => { alive = false; };
  }, [session]);

  useEffect(() => {
    if (session && loadedUid === session.uid) AsyncStorage.setItem(`arribo:${DEMO_MODE ? 'demo' : 'live'}:${session.uid}`, JSON.stringify({ history, alerts, preferences, pending })).catch(() => setError('No se pudieron guardar los datos en el dispositivo.'));
  }, [session, loadedUid, history, alerts, preferences, pending]);

  const refreshHistory = useCallback(async (more = false) => {
    if (!session || session.demo || !online || (more && !cursor.current)) return;
    const uid = session.uid;
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
  }, [session, online]);

  useEffect(() => {
    if (!session || session.demo || loadedUid !== session.uid || !online) return;
    const uid = session.uid; const { db } = firebaseServices();
    refreshHistory().catch(failure => setError(friendlyError(failure)));
    getDoc(doc(db, 'users', uid)).then(snapshot => {
      if (activeUid.current !== uid) return;
      if (snapshot.exists()) setPreferences({ ...DEFAULT_PREFERENCES, ...snapshot.data().preferences });
      else return ensureProfile(uid, session.email);
    }).catch(failure => setError(friendlyError(failure)));
    return onSnapshot(query(collection(db, 'alerts'), where('station_id', '==', STATION.id), where('active', '==', true)), snapshot => {
      if (activeUid.current !== uid) return;
      setAlerts(snapshot.docs.map(item => {
        const data = item.data();
        return { ...data, id: item.id, created_at: toISO(data.created_at), expires_at: toISO(data.expires_at) } as Alert;
      }));
    }, failure => setError(friendlyError(failure)));
  }, [session, loadedUid, online, refreshHistory]);

  const retryPending = useCallback(async () => {
    if (!session || session.demo || !online || loadedUid !== session.uid || sending.current) return;
    sending.current = true; const uid = session.uid;
    try {
      for (const report of [...queueRef.current]) {
        await apiRequest('/feedback', report);
        if (activeUid.current !== uid) return;
        queueRef.current = queueRef.current.filter(item => item.query_id !== report.query_id); setPending([...queueRef.current]);
        setHistory(previous => previous.map(item => item.query_id === report.query_id ? { ...item, actual_min: report.actual_min, feedback_pending: false } : item));
      }
    } catch (failure) { if (activeUid.current === uid) setError(friendlyError(failure)); }
    finally { sending.current = false; }
  }, [session, online, loadedUid]);
  useEffect(() => { void retryPending(); }, [retryPending]);

  async function login(email: string, password: string, register = false) {
    try {
      const { auth } = firebaseServices();
      const credential = await (register ? createUserWithEmailAndPassword(auth, email.trim(), password) : signInWithEmailAndPassword(auth, email.trim(), password));
      if (register) await ensureProfile(credential.user.uid, credential.user.email);
    } catch (failure) { throw new Error(friendlyError(failure)); }
  }
  async function enterDemo() {
    const user: Session = { uid: 'local-demo', email: null, demo: true };
    await AsyncStorage.setItem('arribo:demo-session', JSON.stringify(user)); setSession(user); setError(null);
  }
  async function logout() {
    activeUid.current = null;
    if (session) await AsyncStorage.setItem(`arribo:${DEMO_MODE ? 'demo' : 'live'}:${session.uid}`, JSON.stringify({ history, alerts, preferences, pending: queueRef.current }));
    if (session?.demo) await AsyncStorage.removeItem('arribo:demo-session'); else await signOut(firebaseServices().auth);
    setSession(null); setError(null);
  }
  async function predict() {
    if (!session || loadedUid !== session.uid) throw new Error('Espera a que se cargue tu sesión.');
    if (!online && !session.demo) throw new Error('Necesitas conexión para obtener una predicción actual.');
    const uid = session.uid;
    const id = requestId.current ?? `query-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`; requestId.current = id;
    const result = session.demo ? await demoPrediction(id, scenario) : validatePrediction(await apiRequest<Prediction>('/predict', { station_id: STATION.id, request_id: id }));
    if (activeUid.current !== uid) throw new Error('La sesión cambió. Vuelve a consultar.');
    requestId.current = null; setHistory(previous => [{ ...result, requested_at: result.requested_at ?? new Date().toISOString() }, ...previous.filter(item => item.query_id !== result.query_id)]);
    return result;
  }
  async function feedback(queryId: string, minutes: number): Promise<'sent' | 'queued'> {
    if (!session || !history.some(item => item.query_id === queryId)) throw new Error('No se encontró la consulta.');
    if (!Number.isFinite(minutes) || minutes < 0 || minutes > 180) throw new Error('Ingresa una espera entre 0 y 180 minutos.');
    const uid = session.uid; const report = { query_id: queryId, actual_min: minutes };
    if (!session.demo && online) {
      try {
        await apiRequest('/feedback', report);
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
  return <Context.Provider value={{ booting, session, online, mode: DEMO_MODE ? 'demo' : 'live', history, alerts, preferences, scenario, error, dataReady: !!session && loadedUid === session.uid, hasMore, pendingCount: pending.length, login, enterDemo, logout, resetPassword: async email => { try { await sendPasswordResetEmail(firebaseServices().auth, email.trim()); } catch (failure) { throw new Error(friendlyError(failure)); } }, predict, feedback, updatePreferences, refreshHistory, retryPending, setScenario }}>{children}</Context.Provider>;
}
export function useArribo() {
  const state = useContext(Context); if (!state) throw new Error('ArriboProvider no está disponible.'); return state;
}
