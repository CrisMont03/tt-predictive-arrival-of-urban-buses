import 'firebase/auth';
import type AsyncStorage from '@react-native-async-storage/async-storage';
import type { Persistence } from 'firebase/auth';

// Firebase's RN export includes this API; its default (web) declarations omit it.
declare module 'firebase/auth' {
  export function getReactNativePersistence(storage: Pick<typeof AsyncStorage, 'getItem' | 'setItem' | 'removeItem'>): Persistence;
}
