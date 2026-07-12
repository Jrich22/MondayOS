import { useMemo } from "react";
import { qrMatrix } from "@/lib/qr";
import { cn } from "@/lib/cn";

/**
 * Renders a payload's QR-style glyph as crisp, self-contained SVG (no image, no
 * library, no network — it prints and exports cleanly). The modules come from the
 * deterministic matrix in lib/qr, so the same badge always shows the same glyph.
 * It is decorative-scannable: a real camera reader isn't wired in the MVP, but it
 * reads unmistakably as a QR credential, finder squares and all.
 */
export function QrGlyph({
  payload,
  size = 25,
  className,
}: {
  payload: string;
  size?: number;
  className?: string;
}) {
  const matrix = useMemo(() => qrMatrix(payload, size), [payload, size]);
  const quiet = 2;
  const dim = size + quiet * 2;

  return (
    <svg
      viewBox={`0 0 ${dim} ${dim}`}
      role="img"
      aria-label="Attendee QR credential"
      shapeRendering="crispEdges"
      className={cn("h-full w-full", className)}
    >
      <rect width={dim} height={dim} fill="#ffffff" />
      {matrix.map((row, r) =>
        row.map((on, c) =>
          on ? (
            <rect key={`${r}-${c}`} x={c + quiet} y={r + quiet} width={1} height={1} fill="#0b0d12" />
          ) : null,
        ),
      )}
    </svg>
  );
}
