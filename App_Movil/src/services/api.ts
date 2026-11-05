import { API_URL } from '@/config/arribo';
import { firebaseServices } from '@/services/firebase';
import type { Prediction } from '@/types/arribo';
import { assertFeedbackAcknowledgement, parsePrediction } from './prediction-contract';
import { RequestTimeoutError, withRequestTimeout } from './request-timeout';

export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number) { super(message); }
}

export async function apiRequest<T>(path: string, body?: unknown): Promise<T> {
  if (!API_URL) throw new ApiError('CONFIGURATION', 'Falta configurar la dirección del servicio.', 0);
  const user = firebaseServices().auth.currentUser;
  if (!user) throw new ApiError('UNAUTHENTICATED', 'Inicia sesión para continuar.', 401);
  try {
    return await withRequestTimeout(async signal => {
      const token = await user.getIdToken();
      // Token renewal may finish after the request deadline. Do not send it then.
      if (signal.aborted) throw new RequestTimeoutError();
      const response = await fetch(`${API_URL}${path}`, {
        method: body === undefined ? 'GET' : 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        signal,
      });
      const result = await response.json().catch(() => { throw new ApiError('INVALID_RESPONSE', 'El servicio devolvió una respuesta inválida. Reintenta.', response.status); });
      if (!response.ok) {
        const failure = result && typeof result === 'object' ? result : {};
        throw new ApiError(typeof failure.code === 'string' ? failure.code : 'SERVICE_ERROR',
          typeof failure.message === 'string' ? failure.message : 'No se pudo completar la solicitud.', response.status);
      }
      return result as T;
    });
  } catch (error) {
    if (error instanceof RequestTimeoutError) throw new ApiError('TIMEOUT', error.message, 408);
    if (error instanceof ApiError) throw error;
    throw new ApiError('NETWORK', 'No se pudo conectar. Comprueba tu conexión e inténtalo de nuevo.', 0);
  }
}

export function validatePrediction(result: unknown, expected?: { queryId: string; stationId: string }): Prediction {
  try { return parsePrediction(result, expected); } catch {
    throw new ApiError('INVALID_RESPONSE', 'El servicio devolvió una respuesta incompleta.', 502);
  }
}

export async function sendFeedback(queryId: string, actualMin: number, waitBasis?: 'since_query' | 'unspecified') {
  const result = await apiRequest('/feedback', { query_id: queryId, actual_min: actualMin, ...(waitBasis ? { wait_basis: waitBasis } : {}) });
  try { assertFeedbackAcknowledgement(result, queryId); } catch {
    throw new ApiError('INVALID_RESPONSE', 'No se pudo confirmar el reporte. Reintenta.', 502);
  }
}
