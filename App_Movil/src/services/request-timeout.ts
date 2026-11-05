// Allow Cloud Run to start and load the LSTM after a period without requests.
export const REQUEST_TIMEOUT_MS = 90_000;

export class RequestTimeoutError extends Error {
  constructor() {
    super('El servicio tardó demasiado en responder. Puede estar iniciándose. Espera un momento e inténtalo de nuevo.');
    this.name = 'RequestTimeoutError';
  }
}

export async function withRequestTimeout<T>(work: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      reject(new RequestTimeoutError());
      controller.abort();
    }, REQUEST_TIMEOUT_MS);
  });
  try {
    return await Promise.race([work(controller.signal), timeout]);
  } finally {
    clearTimeout(timer);
  }
}
