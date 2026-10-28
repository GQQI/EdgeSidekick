import type { SVGProps } from "react";

type Props = SVGProps<SVGSVGElement> & { size?: number };

/** Neutral robot holding a rotating cube. */
export function IconRobotCube({ size = 28, className, ...rest }: Props) {
  const id = "rk";
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      className={`icon-robot-cube ${className || ""}`.trim()}
      aria-hidden
      {...rest}
    >
      <defs>
        <linearGradient id={`${id}-body`} x1="12" y1="10" x2="52" y2="54" gradientUnits="userSpaceOnUse">
          <stop stopColor="#6b7280" />
          <stop offset="1" stopColor="#1f2937" />
        </linearGradient>
        <linearGradient id={`${id}-cube`} x1="36" y1="8" x2="58" y2="30" gradientUnits="userSpaceOnUse">
          <stop stopColor="#e5e7eb" />
          <stop offset="1" stopColor="#6b7280" />
        </linearGradient>
      </defs>

      <g className="rc-antenna">
        <line x1="32" y1="10" x2="32" y2="16" stroke="#1f2937" strokeWidth="2.2" strokeLinecap="round" />
        <circle cx="32" cy="8" r="2.4" fill="#9ca3af" />
      </g>

      <rect x="16" y="16" width="32" height="28" rx="8" fill={`url(#${id}-body)`} />
      <rect x="20" y="22" width="24" height="12" rx="4" fill="#f9fafb" opacity="0.95" />
      <g className="rc-eyes">
        <circle cx="27" cy="28" r="2.2" fill="#111827" />
        <circle cx="37" cy="28" r="2.2" fill="#111827" />
      </g>
      <path d="M26 34.5c2.2 2 9.8 2 12 0" stroke="#111827" strokeWidth="1.8" strokeLinecap="round" />

      <path d="M16 30c-5 2-7 8-5 12" stroke="#1f2937" strokeWidth="3.2" strokeLinecap="round" />
      <path d="M48 28c4-1 9 2 10 7" stroke="#1f2937" strokeWidth="3.2" strokeLinecap="round" />

      <g className="rc-cube" transform="translate(46 18)">
        <g className="rc-cube-spin">
          <path d="M0 6 L8 1 L16 6 L8 11 Z" fill={`url(#${id}-cube)`} />
          <path d="M0 6 L8 11 L8 19 L0 14 Z" fill="#4b5563" />
          <path d="M16 6 L8 11 L8 19 L16 14 Z" fill="#1f2937" />
          <path d="M0 6 L8 1 L16 6 L8 11 Z" stroke="#f3f4f6" strokeWidth="0.6" opacity="0.7" />
        </g>
      </g>

      <path d="M24 44v8M40 44v8" stroke="#1f2937" strokeWidth="3.2" strokeLinecap="round" />
      <circle cx="24" cy="54" r="2.4" fill="#9ca3af" />
      <circle cx="40" cy="54" r="2.4" fill="#9ca3af" />
    </svg>
  );
}
