import { sandboxUrlGesture, splitTextWithUrls } from "../browser/urlDetect";

type Props = {
  text: string;
  className?: string;
  onCtrlClickUrl?: (url: string, clientX: number, clientY: number) => void;
};

const LINK_TITLE = "右键或 Ctrl+单击：在浏览器沙盒打开";

/** Plain text with http(s) URLs; right-click / Ctrl+click offers sandbox open. */
export function LinkifiedText({ text, className, onCtrlClickUrl }: Props) {
  const parts = splitTextWithUrls(text);
  return (
    <pre className={className}>
      {parts.map((p, i) => {
        if (p.type === "text") {
          return <span key={i}>{p.value}</span>;
        }
        return (
          <a
            key={i}
            href={p.href}
            className="sandbox-hot-link"
            title={LINK_TITLE}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => {
              sandboxUrlGesture(p.href, e, onCtrlClickUrl, { mode: "click" });
            }}
            onContextMenu={(e) => {
              sandboxUrlGesture(p.href, e, onCtrlClickUrl, { mode: "contextmenu" });
            }}
          >
            {p.value}
          </a>
        );
      })}
    </pre>
  );
}
