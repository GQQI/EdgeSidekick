import { useState } from "react";
import type { SocialPlanCard } from "./types";

const MODE_ICON: Record<string, string> = {
  answer: "◎",
  parallel: "⫴",
  pipeline: "→",
  discuss: "◌",
  chat: "·",
};

export function CollabPlanCard({ card }: { card: SocialPlanCard }) {
  const [open, setOpen] = useState(card.steps.length <= 3);
  const connector = card.mode === "pipeline" ? "→" : "·";
  return (
    <div className="collab-plan-card" role="group" aria-label="本轮协作分工">
      <button type="button" className="collab-plan-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="collab-plan-mode">
          <span aria-hidden>{MODE_ICON[card.mode] || "◎"}</span>
          {card.label}
        </span>
        <span className="collab-plan-goal">{card.goal}</span>
        <span className="collab-plan-toggle" aria-hidden>{open ? "收起" : "展开"}</span>
      </button>
      {open ? (
        <>
          <ol className={"collab-plan-steps" + (card.mode === "pipeline" ? " is-pipeline" : "")}>
            {card.steps.map((step, i) => (
              <li key={step.id}>
                {i > 0 && card.mode === "pipeline" ? <span className="collab-plan-arrow" aria-hidden>{connector}</span> : null}
                <span className="collab-plan-who">{step.name}</span>
                <span className="collab-plan-task">{step.task}</span>
                {step.expected ? <span className="collab-plan-expected">交付：{step.expected}</span> : null}
              </li>
            ))}
          </ol>
          {card.review || card.synthesizer ? (
            <div className="collab-plan-foot">
              {card.review ? <span>复核：{card.review}</span> : null}
              {card.synthesizer ? <span>汇总：{card.synthesizer}</span> : null}
            </div>
          ) : null}
          {card.reason ? <div className="collab-plan-reason">{card.reason}</div> : null}
        </>
      ) : null}
    </div>
  );
}
