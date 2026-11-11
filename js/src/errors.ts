/** The base of every error this package throws itself. */
export class PydiggsError extends Error {
  override name: string = "PydiggsError";
}
