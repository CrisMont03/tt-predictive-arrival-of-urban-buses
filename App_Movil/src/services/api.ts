import { API_URL } from '@/config/arribo';
import { firebaseServices } from '@/services/firebase';
import type { Prediction } from '@/types/arribo';

export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number) { super(message); }
}

export async function apiRequest<T>(path: string, body?: unknown): Promise<T> {
  if (!API_URL) throw new ApiError('CONFIGURATION', 'Falta configurar la dirección del servicio.', 0);
  const user = firebaseServices().auth.currentUser;
  if (!user) throw new ApiError('UNAUTHENTICATED', 'Inicia sesión para continuar.', 401);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 12000);
  try {
    const token = await user.getIdToken();
    const response = await fetch(`${API_URL}${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: controller.signal,
    });
    const result = await response.json();
    if (!response.ok) throw new ApiError(result.code ?? 'SERVICE_ERROR', result.message ?? 'No se pudo completar la solicitud.', response.status);
    return result as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError('NETWORK', 'No se pudo conectar. Comprueba tu conexión e inténtalo de nuevo.', 0);
  } finally { clearTimeout(timer); }
}

export function validatePrediction(result: Prediction): Prediction {
  if (!result.query_id || !Number.isFinite(result.waiting_time_min) || result.waiting_time_min < 0 ||
      !Array.isArray(result.confidence_interval) || result.confidence_interval.length !== 2 ||
      result.confidence_interval.some(n => !Number.isFinite(n) || n < 0) ||
      result.confidence_interval[0] > result.confidence_interval[1] ||
      !Number.isFinite(Date.parse(result.timestamp)) || !Number.isFinite(Date.parse(result.expires_at)) ||
      !result.context || !['mock', 'cache', 'model'].includes(result.source) || typeof result.simulation !== 'boolean') {
    throw new ApiError('INVALID_RESPONSE', 'El servicio devolvió una respuesta incompleta.', 502);
  }
  return result;
}
