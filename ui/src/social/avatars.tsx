export function AgentAvatar({
  agent,
  size = 36,
  className = "social-avatar",
}: {
  agent: { name: string; color: string; avatar?: string };
  size?: number;
  className?: string;
}) {
  if (agent.avatar) {
    return (
      <img
        className={className}
        src={agent.avatar}
        alt={agent.name}
        style={{ width: size, height: size, borderRadius: "50%", objectFit: "cover" }}
      />
    );
  }
  return (
    <span className={className} style={{ background: agent.color, width: size, height: size }}>
      {agent.name.slice(0, 1)}
    </span>
  );
}

export function GroupAvatar({
  group,
  members,
  size = 44,
}: {
  group: { name: string; avatar?: string };
  members: { name: string; color: string; avatar?: string }[];
  size?: number;
}) {
  if (group.avatar) {
    return (
      <img
        className="social-avatar social-avatar-group-img"
        src={group.avatar}
        alt={group.name}
        style={{ width: size, height: size, borderRadius: 8, objectFit: "cover" }}
      />
    );
  }
  const cells = members.slice(0, 9);
  const n = Math.max(cells.length, 1);
  const cols = n <= 1 ? 1 : n <= 4 ? 2 : 3;
  const list = cells.length ? cells : [{ name: group.name, color: "#64748b" }];
  return (
    <span
      className="social-avatar-mosaic"
      style={{ width: size, height: size, gridTemplateColumns: "repeat(" + cols + ", 1fr)" }}
      aria-label={group.name}
    >
      {list.map((m, idx) =>
        m.avatar ? (
          <img key={idx} src={m.avatar} alt="" />
        ) : (
          <span key={idx} style={{ background: m.color || "#64748b" }}>
            {(m.name || "?").slice(0, 1)}
          </span>
        ),
      )}
    </span>
  );
}
