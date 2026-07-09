/**
 * Mock session (auth placeholder — TASK-0031).
 *
 * There is NO production authentication in the MVP (see DEC-0003). This provides
 * a single hardcoded "current user" so surfaces that need an identity have a
 * stable seam to read from. When real auth lands, only this module changes.
 */
export interface CurrentUser {
  name: string;
  role: string;
  firm: string;
  initials: string;
}

export const currentUser: CurrentUser = {
  name: "Dana Whitfield",
  role: "Platform Lead",
  firm: "Redpoint Ventures",
  initials: "DW",
};
