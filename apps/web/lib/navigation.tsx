import { Link as RouterLink, useLocation, useNavigate, useRouter as useTanStackRouter } from "@tanstack/react-router";
import { useMemo, type AnchorHTMLAttributes, type RefAttributes } from "react";
export { notFound, redirect } from "@tanstack/react-router";

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & RefAttributes<HTMLAnchorElement> & {
  href: string; prefetch?: boolean; replace?: boolean; scroll?: boolean;
};
/** Shared links accept existing validated URLs; the router owns in-app history. */
export default function Link({ href, prefetch = false, replace, scroll = true, ...props }: LinkProps) {
  if (!href.startsWith("/") || href.startsWith("//") || props.download || props.target)
    return <a href={href} {...props} />;
  return <RouterLink to={href} preload={prefetch ? "intent" : false} replace={replace} resetScroll={scroll} activeOptions={{ exact: true, includeSearch: false }} activeProps={{}} {...props} />;
}
export function usePathname() { return useLocation({ select: location => location.pathname }); }
export function useSearchParams() {
  const search = useLocation({ select: location => location.searchStr });
  return useMemo(() => new URLSearchParams(search), [search]);
}
export function useRouter() {
  const navigate = useNavigate();
  const router = useTanStackRouter();
  return useMemo(() => ({
    push: (href: string, options?: { scroll?: boolean }) => { void navigate({ href, resetScroll: options?.scroll ?? true }); },
    replace: (href: string, options?: { scroll?: boolean }) => { void navigate({ href, replace: true, resetScroll: options?.scroll ?? true }); },
    back: () => router.history.back(),
    refresh: () => { void router.invalidate(); },
  }), [navigate, router]);
}
