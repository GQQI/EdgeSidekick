import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function base({ size = 16, ...rest }: IconProps) {
  return {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    "aria-hidden": true as const,
    ...rest,
  };
}

const stroke = {
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export function IconClock(p: IconProps) {
  return (
    <svg {...base(p)}>
      <circle cx="12" cy="12" r="9" {...stroke} />
      <path d="M12 7v5l3 2" {...stroke} />
    </svg>
  );
}

export function IconSettings(p: IconProps) {
  return (
    <svg {...base(p)}>
      <circle cx="12" cy="12" r="3" {...stroke} />
      <path
        d="M12 3v2M12 19v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M3 12h2M19 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"
        {...stroke}
      />
    </svg>
  );
}

export function IconPlus(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M12 5v14M5 12h14" {...stroke} />
    </svg>
  );
}

export function IconRefresh(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M21 12a9 9 0 1 1-2.6-6.3" {...stroke} />
      <path d="M21 4v5h-5" {...stroke} />
    </svg>
  );
}

export function IconFolder(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M3 7.5A1.5 1.5 0 0 1 4.5 6H9l2 2h8.5A1.5 1.5 0 0 1 21 9.5v7A1.5 1.5 0 0 1 19.5 18h-15A1.5 1.5 0 0 1 3 16.5v-9Z" {...stroke} />
    </svg>
  );
}

export function IconFile(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V8l-5-5Z" {...stroke} />
      <path d="M14 3v5h5" {...stroke} />
    </svg>
  );
}

export function IconChat(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M5 18.5 3 21V7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v9.5a2 2 0 0 1-2 2H5Z" {...stroke} />
    </svg>
  );
}

export function IconRobot(p: IconProps) {
  return (
    <svg {...base(p)}>
      <rect x="5" y="8" width="14" height="11" rx="2.5" {...stroke} />
      <path d="M12 4v4M9 13h.01M15 13h.01M9 16.5h6" {...stroke} />
    </svg>
  );
}

export function IconUser(p: IconProps) {
  return (
    <svg {...base(p)}>
      <circle cx="12" cy="8" r="3.5" {...stroke} />
      <path d="M5 19.5a7 7 0 0 1 14 0" {...stroke} />
    </svg>
  );
}

export function IconSearch(p: IconProps) {
  return (
    <svg {...base(p)}>
      <circle cx="11" cy="11" r="6.5" {...stroke} />
      <path d="M16.5 16.5 21 21" {...stroke} />
    </svg>
  );
}

export function IconBinoculars(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M4 10h4l1.5 8H5.5L4 10Zm12 0h4l-1.5 8h-4L16 10Z" {...stroke} />
      <path d="M8 10h8M9 6h6" {...stroke} />
    </svg>
  );
}

export function IconBraces(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M9 5c-2 0-3 1.5-3 3.5S5 12 5 12s1 1.5 1 3.5S7 19 9 19M15 5c2 0 3 1.5 3 3.5S19 12 19 12s-1 1.5-1 3.5S17 19 15 19" {...stroke} />
    </svg>
  );
}

export function IconAt(p: IconProps) {
  return (
    <svg {...base(p)}>
      <circle cx="12" cy="12" r="8.5" {...stroke} />
      <path d="M16 12a4 4 0 1 1-1.2-2.9V13a2 2 0 0 0 3.5 1.3" {...stroke} />
    </svg>
  );
}

export function IconPaperclip(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="m15.5 7.5-6.8 6.8a2.5 2.5 0 0 0 3.5 3.5l7.2-7.2a4 4 0 0 0-5.7-5.7L6.5 12.1a5.5 5.5 0 0 0 7.8 7.8l5.2-5.2" {...stroke} />
    </svg>
  );
}

export function IconWand(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="m15 4 1.2 2.8L19 8l-2.8 1.2L15 12l-1.2-2.8L11 8l2.8-1.2L15 4Z" {...stroke} />
      <path d="m5 19 8-8M4 10h.01M10 4h.01" {...stroke} />
    </svg>
  );
}

export function IconSend(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M21 4 10.5 14.5M21 4l-7 17-3.5-6.5L4 11l17-7Z" {...stroke} />
    </svg>
  );
}

export function IconCheck(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="m5 12 5 5L20 7" {...stroke} />
    </svg>
  );
}

export function IconX(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M6 6l12 12M18 6 6 18" {...stroke} />
    </svg>
  );
}

export function IconChevronDown(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="m6 9 6 6 6-6" {...stroke} />
    </svg>
  );
}

export function IconChevronRight(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="m9 6 6 6-6 6" {...stroke} />
    </svg>
  );
}

export function IconTrash(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M4 7h16M9 7V5h6v2M8 7l1 13h6l1-13" {...stroke} />
    </svg>
  );
}

export function IconUndo(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M9 14 4 9l5-5" {...stroke} />
      <path d="M4 9h10a5 5 0 1 1 0 10h-3" {...stroke} />
    </svg>
  );
}

export function IconExternal(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M14 4h6v6M20 4 11 13M10 5H6a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-4" {...stroke} />
    </svg>
  );
}

export function IconSun(p: IconProps) {
  return (
    <svg {...base(p)}>
      <circle cx="12" cy="12" r="4" {...stroke} />
      <path d="M12 2v2.5M12 19.5V22M4.93 4.93l1.77 1.77M17.3 17.3l1.77 1.77M2 12h2.5M19.5 12H22M4.93 19.07l1.77-1.77M17.3 6.7l1.77-1.77" {...stroke} />
    </svg>
  );
}

export function IconMoon(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5Z" {...stroke} />
    </svg>
  );
}

export function IconFiles(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M8 5h9a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Z" {...stroke} />
      <path d="M6 8H5a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h9" {...stroke} />
    </svg>
  );
}

export function IconGlobe(p: IconProps) {
  return (
    <svg {...base(p)}>
      <circle cx="12" cy="12" r="9" {...stroke} />
      <path
        d="M3 12h18M12 3c2.5 2.5 3.8 5.5 3.8 9S14.5 18.5 12 21c-2.5-2.5-3.8-5.5-3.8-9S9.5 5.5 12 3Z"
        {...stroke}
      />
    </svg>
  );
}

export function IconGit(p: IconProps) {
  return (
    <svg {...base(p)}>
      <circle cx="6" cy="6" r="2.2" {...stroke} />
      <circle cx="18" cy="12" r="2.2" {...stroke} />
      <circle cx="6" cy="18" r="2.2" {...stroke} />
      <path d="M6 8.2v7.6M6 12h9.8" {...stroke} />
    </svg>
  );
}

export function IconTerminal(p: IconProps) {
  return (
    <svg {...base(p)}>
      <rect x="3" y="5" width="18" height="14" rx="2" {...stroke} />
      <path d="m7 10 3 2-3 2M13 14h4" {...stroke} />
    </svg>
  );
}

export function IconCalendar(p: IconProps) {
  return (
    <svg {...base(p)}>
      <rect x="3" y="5" width="18" height="16" rx="2" {...stroke} />
      <path d="M8 3v4M16 3v4M3 10h18" {...stroke} />
      <path d="M8 15h.01M12 15h.01M16 15h.01M8 18h.01M12 18h.01" {...stroke} />
    </svg>
  );
}

export function IconCube(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z" {...stroke} />
      <path d="M12 12 20 7.5M12 12v9M12 12 4 7.5" {...stroke} />
    </svg>
  );
}

export function IconPin(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M9 10.5 8 17l4-2 4 2-1-6.5" {...stroke} />
      <path d="M9 10.5V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5v5" {...stroke} />
      <path d="M12 15v5" {...stroke} />
    </svg>
  );
}

export function IconBook(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v16H6.5A1.5 1.5 0 0 0 5 20.5V4.5Z" {...stroke} />
      <path d="M5 19.5A1.5 1.5 0 0 1 6.5 18H19" {...stroke} />
    </svg>
  );
}

export function IconPencil(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M13 5.5 18.5 11M4 20l3.2-.7L19 7.5 13.5 2 4.7 10.8 4 20Z" {...stroke} />
    </svg>
  );
}

export function IconEye(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" {...stroke} />
      <circle cx="12" cy="12" r="3" {...stroke} />
    </svg>
  );
}

export function IconEyeOff(p: IconProps) {
  return (
    <svg {...base(p)}>
      <path d="M3 3l18 18M10.6 10.6A3 3 0 0 0 12 15a3 3 0 0 0 2.4-1.2M9.9 5.1A11 11 0 0 1 12 5c6.5 0 10 7 10 7a18 18 0 0 1-2.2 3.1M6.1 6.1C3.6 8 2 12 2 12s3.5 7 10 7a10 10 0 0 0 4.2-.9" {...stroke} />
    </svg>
  );
}
