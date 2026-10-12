import { useId } from "react";

type Props = {
  size?: number;
  className?: string;
};

/**
 * EdgeSidekick mark — single-agent AgentFrame style:
 * blue gradient rounded square + one robot (antenna, white body, two eyes).
 * No partner robots (multi-agent).
 */
export function BrandMark({ size = 32, className = "" }: Props) {
  const gid = `esk-${useId().replace(/:/g, "")}`;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      className={`brand-icon ${className}`.trim()}
      role="img"
      aria-label="EdgeSidekick"
    >
      <defs>
        <linearGradient id={gid} x1="8" y1="4" x2="56" y2="60" gradientUnits="userSpaceOnUse">
          <stop stopColor="#5b97ff" />
          <stop offset="0.55" stopColor="#2f6fed" />
          <stop offset="1" stopColor="#1d4fc4" />
        </linearGradient>
      </defs>
      <rect className="brand-plate" width="64" height="64" rx="15" fill={`url(#${gid})`} />
      <path className="brand-antenna" d="M32 21V15" stroke="#fff" strokeWidth="4.4" strokeLinecap="round" />
      <circle className="brand-antenna-tip" cx="32" cy="12" r="4.8" fill="#fff" />
      <rect className="brand-body" x="18" y="21" width="28" height="31" rx="9.5" fill="#fff" />
      <circle className="brand-eye" cx="26.5" cy="37" r="4.6" fill="#2f6fed" />
      <circle className="brand-eye" cx="37.5" cy="37" r="4.6" fill="#2f6fed" />
    </svg>
  );
}