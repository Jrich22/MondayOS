import type { SVGProps } from "react";
import {
  CalendarIcon,
  MailIcon,
  BellIcon,
  StarIcon,
  MegaphoneIcon,
  MoonIcon,
  SunIcon,
  HeartIcon,
  ClipboardIcon,
  DocumentIcon,
  EyeIcon,
  CursorClickIcon,
  CheckIcon,
  CloseIcon,
  CheckCircleIcon,
} from "@/components/icons";

/**
 * Maps the string icon keys used by lib/comms (stage metadata) and
 * lib/comms-history (timeline entries) to concrete icon components, so those
 * pure modules stay presentation-free. One lookup table keeps the mapping in a
 * single place as stages/touchpoints are added.
 */

type IconProps = SVGProps<SVGSVGElement>;
type IconFn = (p: IconProps) => JSX.Element;

const MAP: Record<string, IconFn> = {
  // Stage icons
  calendar: CalendarIcon,
  mail: MailIcon,
  bell: BellIcon,
  star: StarIcon,
  megaphone: MegaphoneIcon,
  moon: MoonIcon,
  sun: SunIcon,
  heart: HeartIcon,
  clipboard: ClipboardIcon,
  document: DocumentIcon,
  // Timeline-only icons
  eye: EyeIcon,
  cursor: CursorClickIcon,
  check: CheckIcon,
  close: CloseIcon,
  checkin: CheckCircleIcon,
};

/** Resolve an icon key to a rendered icon, defaulting to a neutral dot glyph. */
export function CommsIcon({ name, ...props }: { name: string } & IconProps) {
  const Icon = MAP[name] ?? DocumentIcon;
  return <Icon {...props} />;
}
