// Stub (M7 RED): implemented in the feat commit.
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}
export function configureApi(_opts: { onUnauthorized?: () => void }): void {}
export async function request<T>(_method: string, _path: string, _body?: unknown): Promise<T> {
  throw new Error("not implemented");
}
