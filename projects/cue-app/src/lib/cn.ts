/** Tiny classpath joiner — filters out falsey values so conditional classes stay readable. */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
