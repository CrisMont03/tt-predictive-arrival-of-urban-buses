import AsyncStorage from '@react-native-async-storage/async-storage';
import { getApps, initializeApp } from 'firebase/app';
import { getAuth, getReactNativePersistence, initializeAuth, type Auth } from 'firebase/auth';
import { getFirestore } from 'firebase/firestore';
import { Platform } from 'react-native';

export function firebaseServices() {
  const apiKey = process.env.EXPO_PUBLIC_FIREBASE_API_KEY;
  const projectId = process.env.EXPO_PUBLIC_FIREBASE_PROJECT_ID;
  const appId = process.env.EXPO_PUBLIC_FIREBASE_APP_ID;
  if (!apiKey || !projectId || !appId) throw new Error('Falta configurar Firebase. Consulta .env.example.');
  const app = getApps()[0] ?? initializeApp({
    apiKey, projectId, appId,
    authDomain: process.env.EXPO_PUBLIC_FIREBASE_AUTH_DOMAIN,
  });
  let auth: Auth;
  try {
    auth = Platform.OS === 'web' ? getAuth(app) : initializeAuth(app, { persistence: getReactNativePersistence(AsyncStorage) });
  } catch (error) {
    if ((error as { code?: string }).code !== 'auth/already-initialized') throw error;
    auth = getAuth(app);
  }
  return { auth, db: getFirestore(app) };
}
