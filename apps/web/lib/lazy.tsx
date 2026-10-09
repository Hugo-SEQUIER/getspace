import { lazy, Suspense, type ComponentType, type ReactNode } from "react";
export default function dynamic<P extends object>(load: () => Promise<ComponentType<P>>, options?: { loading?: () => ReactNode }) {
  const Component = lazy(async () => ({ default: await load() }));
  return function LazyComponent(props: P) { return <Suspense fallback={options?.loading?.() ?? null}><Component {...props} /></Suspense>; };
}
