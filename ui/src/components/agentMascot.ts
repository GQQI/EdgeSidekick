/** Compact port of 修正版_AI机器人椅子打字动画.html for AgentCanvas cards. */

export type MascotFace = "idle" | "work" | "happy" | "wink" | "wow";

const C = {
  ink: "#252A33",
  ink2: "#3C4350",
  shell: "#FAFBFD",
  shellShade: "#E7EAF0",
  chair: "#D4D9E3",
  chairEdge: "#C0C7D2",
  seat: "#BBC2CE",
  desk: "#F0F2F6",
  deskEdge: "#DDE2EA",
  blue: "#8CB8FF",
  bluePale: "#EDF4FF",
  blueLine: "#C9DDFE",
  screen: "#303744",
  key: "#D9DEE7",
};

function rr(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
  fill?: string,
  stroke?: string | null,
  lw = 1,
) {
  const radius = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + w, y, x + w, y + h, radius);
  ctx.arcTo(x + w, y + h, x, y + h, radius);
  ctx.arcTo(x, y + h, x, y, radius);
  ctx.arcTo(x, y, x + w, y, radius);
  ctx.closePath();
  if (fill) {
    ctx.fillStyle = fill;
    ctx.fill();
  }
  if (stroke) {
    ctx.strokeStyle = stroke;
    ctx.lineWidth = lw;
    ctx.stroke();
  }
}

function el(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  rx: number,
  ry: number,
  fill?: string,
  stroke?: string | null,
  lw = 1,
) {
  ctx.beginPath();
  ctx.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
  if (fill) {
    ctx.fillStyle = fill;
    ctx.fill();
  }
  if (stroke) {
    ctx.strokeStyle = stroke;
    ctx.lineWidth = lw;
    ctx.stroke();
  }
}

function ln(ctx: CanvasRenderingContext2D, ps: number[][], color: string, width = 2) {
  if (ps.length < 2) return;
  ctx.beginPath();
  ctx.moveTo(ps[0][0], ps[0][1]);
  for (let i = 1; i < ps.length; i++) ctx.lineTo(ps[i][0], ps[i][1]);
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.stroke();
}

function poly(
  ctx: CanvasRenderingContext2D,
  ps: number[][],
  fill?: string,
  stroke?: string | null,
  lw = 1,
) {
  if (ps.length < 2) return;
  ctx.beginPath();
  ctx.moveTo(ps[0][0], ps[0][1]);
  for (let i = 1; i < ps.length; i++) ctx.lineTo(ps[i][0], ps[i][1]);
  ctx.closePath();
  if (fill) {
    ctx.fillStyle = fill;
    ctx.fill();
  }
  if (stroke) {
    ctx.strokeStyle = stroke;
    ctx.lineWidth = lw;
    ctx.lineJoin = "round";
    ctx.stroke();
  }
}

function drawAntenna(ctx: CanvasRenderingContext2D, cx: number, baseY: number) {
  ln(ctx, [[cx, baseY], [cx, baseY - 14]], C.ink, 4);
  rr(ctx, cx - 9, baseY - 17, 18, 5, 2, C.ink);
  el(ctx, cx, baseY - 24, 8, 8, C.ink, C.ink2, 1);
  el(ctx, cx - 2, baseY - 27, 2, 2, "#7A8290");
}

function drawHeadBack(ctx: CanvasRenderingContext2D, cx: number, cy: number, tilt = 0) {
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(tilt);
  rr(ctx, -56, -12, 19, 38, 9, C.ink);
  rr(ctx, 37, -12, 19, 38, 9, C.ink);
  rr(ctx, -51, -5, 9, 25, 4, C.ink2);
  rr(ctx, 42, -5, 9, 25, 4, C.ink2);
  el(ctx, 0, 0, 43, 41, C.shell, C.shellShade, 1.4);
  ctx.beginPath();
  ctx.ellipse(-7, -7, 30, 28, -0.3, Math.PI * 1.08, Math.PI * 1.78);
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 4;
  ctx.stroke();
  rr(ctx, -15, 32, 30, 9, 4, C.ink);
  drawAntenna(ctx, 0, -39);
  ctx.restore();
}

function drawHeadFront(ctx: CanvasRenderingContext2D, cx: number, cy: number, face: MascotFace) {
  rr(ctx, cx - 56, cy - 12, 19, 38, 9, C.ink);
  rr(ctx, cx + 37, cy - 12, 19, 38, 9, C.ink);
  rr(ctx, cx - 51, cy - 5, 9, 25, 4, C.ink2);
  rr(ctx, cx + 42, cy - 5, 9, 25, 4, C.ink2);
  rr(ctx, cx - 45, cy - 39, 90, 80, 29, C.shell, C.shellShade, 1.4);
  ctx.beginPath();
  ctx.ellipse(cx - 7, cy - 13, 29, 21, -0.25, Math.PI * 1.1, Math.PI * 1.78);
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 4;
  ctx.stroke();
  rr(ctx, cx - 35, cy - 26, 70, 49, 19, C.ink);
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 4;
  ctx.lineCap = "round";
  if (face === "wink") {
    ctx.beginPath();
    ctx.moveTo(cx - 20, cy - 4);
    ctx.lineTo(cx - 6, cy - 4);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(cx + 6, cy + 1);
    ctx.quadraticCurveTo(cx + 6, cy - 11, cx + 13, cy - 9);
    ctx.quadraticCurveTo(cx + 20, cy - 7, cx + 20, cy + 1);
    ctx.stroke();
  } else if (face === "wow") {
    el(ctx, cx - 13, cy - 5, 5, 6, "#fff");
    el(ctx, cx + 13, cy - 5, 5, 6, "#fff");
  } else {
    ctx.beginPath();
    ctx.moveTo(cx - 20, cy + 1);
    ctx.quadraticCurveTo(cx - 20, cy - 11, cx - 13, cy - 9);
    ctx.quadraticCurveTo(cx - 6, cy - 7, cx - 6, cy + 1);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(cx + 6, cy + 1);
    ctx.quadraticCurveTo(cx + 6, cy - 11, cx + 13, cy - 9);
    ctx.quadraticCurveTo(cx + 20, cy - 7, cx + 20, cy + 1);
    ctx.stroke();
  }
  if (face === "happy" || face === "wow") {
    ctx.beginPath();
    ctx.arc(cx, cy + 14, 8, 0.15 * Math.PI, 0.85 * Math.PI);
    ctx.strokeStyle = C.blue;
    ctx.lineWidth = 3;
    ctx.stroke();
  }
  rr(ctx, cx - 13, cy + 36, 26, 8, 4, C.ink);
  drawAntenna(ctx, cx, cy - 42);
}

function drawTorso(ctx: CanvasRenderingContext2D, cx: number, top: number) {
  rr(ctx, cx - 32, top, 64, 69, 20, C.shell, C.shellShade, 1);
  rr(ctx, cx - 21, top + 7, 42, 5, 2, "#fff");
}

function drawSeatedLegsBack(ctx: CanvasRenderingContext2D, cx: number, hipY: number) {
  poly(
    ctx,
    [
      [cx - 18, hipY],
      [cx - 26, hipY + 12],
      [cx - 22, hipY + 28],
      [cx - 8, hipY + 34],
      [cx - 4, hipY + 20],
      [cx + 2, hipY + 8],
    ],
    C.shell,
    C.shellShade,
    1,
  );
  poly(
    ctx,
    [
      [cx + 2, hipY + 8],
      [cx + 8, hipY + 20],
      [cx + 12, hipY + 34],
      [cx + 26, hipY + 28],
      [cx + 30, hipY + 12],
      [cx + 22, hipY],
    ],
    C.shell,
    C.shellShade,
    1,
  );
  rr(ctx, cx - 24, hipY + 28, 14, 20, 5, C.shell, C.shellShade, 1);
  rr(ctx, cx + 10, hipY + 28, 14, 20, 5, C.shell, C.shellShade, 1);
  el(ctx, cx - 17, hipY + 50, 9, 5, C.ink2);
  el(ctx, cx + 17, hipY + 50, 9, 5, C.ink2);
}

function drawSeatedLegsFront(ctx: CanvasRenderingContext2D, cx: number, seatY: number) {
  const hipY = seatY - 4;
  poly(
    ctx,
    [
      [cx - 22, hipY],
      [cx - 26, hipY + 12],
      [cx - 24, hipY + 28],
      [cx - 10, hipY + 32],
      [cx - 4, hipY + 18],
      [cx + 2, hipY + 4],
    ],
    C.shell,
    C.shellShade,
    1,
  );
  poly(
    ctx,
    [
      [cx + 2, hipY + 4],
      [cx + 8, hipY + 18],
      [cx + 14, hipY + 32],
      [cx + 28, hipY + 28],
      [cx + 30, hipY + 12],
      [cx + 26, hipY],
    ],
    C.shell,
    C.shellShade,
    1,
  );
  rr(ctx, cx - 24, hipY + 30, 14, 22, 5, C.shell, C.shellShade, 1);
  rr(ctx, cx + 12, hipY + 30, 14, 22, 5, C.shell, C.shellShade, 1);
  el(ctx, cx - 17, hipY + 54, 9, 5, C.ink2);
  el(ctx, cx + 19, hipY + 54, 9, 5, C.ink2);
}

function drawTypingArms(
  ctx: CanvasRenderingContext2D,
  cx: number,
  shoulderY: number,
  deskTop: number,
  phase: number,
  active: boolean,
) {
  // Rest: both hands still on the keys. Active: alternate press bob.
  const bobL = active && phase === 0 ? 3.5 : active && phase === 2 ? 1.5 : 0;
  const bobR = active && phase === 1 ? 3.5 : active && phase === 2 ? 1.5 : 0;
  const handY = deskTop - 4;
  const arms = [
    { shoulder: cx - 27, elbow: cx - 34, wrist: cx - 22, bob: bobL },
    { shoulder: cx + 27, elbow: cx + 34, wrist: cx + 26, bob: bobR },
  ];
  arms.forEach((a, i) => {
    const elbowY = shoulderY + 26;
    const wristY = handY - a.bob;
    const points = [
      [a.shoulder, shoulderY + 10],
      [a.elbow, elbowY],
      [a.wrist, wristY],
    ];
    ln(ctx, points, C.shellShade, 15);
    ln(ctx, points, C.shell, 11);
    el(ctx, a.wrist, wristY + 1, 7, 5, C.ink2);
    const dir = i === 0 ? 1 : -1;
    ln(ctx, [[a.wrist + dir * 2, wristY + 3], [a.wrist + dir * 8, wristY + 6]], C.shellShade, 2.8);
    ln(ctx, [[a.wrist - dir, wristY + 4], [a.wrist + dir * 5, wristY + 7]], C.shellShade, 2.8);
  });
}

function drawRestingArm(ctx: CanvasRenderingContext2D, cx: number, shoulderY: number) {
  ln(ctx, [[cx - 27, shoulderY + 9], [cx - 42, shoulderY + 25], [cx - 47, shoulderY + 40]], C.shellShade, 15);
  ln(ctx, [[cx - 27, shoulderY + 9], [cx - 42, shoulderY + 25], [cx - 47, shoulderY + 40]], C.shell, 11);
  el(ctx, cx - 47, shoulderY + 40, 6, 5, C.ink2);
}

function drawWaveArm(ctx: CanvasRenderingContext2D, cx: number, shoulderY: number, greet: number) {
  const lift = greet * 0.35;
  ln(
    ctx,
    [
      [cx + 27, shoulderY + 9],
      [cx + 42, shoulderY - 5 - lift],
      [cx + 47, shoulderY - 25 - lift],
    ],
    C.shellShade,
    16,
  );
  ln(
    ctx,
    [
      [cx + 27, shoulderY + 9],
      [cx + 42, shoulderY - 5 - lift],
      [cx + 47, shoulderY - 25 - lift],
    ],
    C.shell,
    12,
  );
  el(ctx, cx + 48, shoulderY - 30 - lift, 9, 10, C.ink);
  rr(ctx, cx + 41, shoulderY - 47 - lift, 7, 15, 3, C.ink);
  rr(ctx, cx + 48, shoulderY - 50 - lift, 7, 16, 3, C.ink);
  rr(ctx, cx + 55, shoulderY - 44 - lift, 6, 12, 3, C.ink);
  rr(ctx, cx + 37, shoulderY - 38 - lift, 7, 11, 3, C.ink);
}

/** Back-facing seat only — no tall backrest so typing hands stay visible. */
function drawChairSeatBackView(ctx: CanvasRenderingContext2D, cx: number, seatY: number) {
  poly(
    ctx,
    [
      [cx - 43, seatY - 8],
      [cx + 48, seatY - 15],
      [cx + 59, seatY + 7],
      [cx - 35, seatY + 17],
    ],
    C.seat,
    C.chairEdge,
    1,
  );
  poly(
    ctx,
    [
      [cx - 38, seatY - 5],
      [cx + 43, seatY - 11],
      [cx + 49, seatY - 3],
      [cx - 33, seatY + 5],
    ],
    "#DDE2EA",
  );
  rr(ctx, cx - 48, seatY - 22, 9, 28, 4, C.ink2);
  rr(ctx, cx + 52, seatY - 24, 10, 28, 4, C.ink2);
  rr(ctx, cx + 48, seatY - 28, 18, 8, 4, C.ink);
  const baseCx = cx + 8;
  rr(ctx, baseCx - 5, seatY + 13, 10, 32, 3, C.ink2);
  el(ctx, baseCx, seatY + 45, 10, 7, C.ink2);
  ln(ctx, [[baseCx, seatY + 45], [baseCx - 48, seatY + 65]], C.ink2, 7);
  ln(ctx, [[baseCx, seatY + 45], [baseCx + 48, seatY + 65]], C.ink2, 7);
  ln(ctx, [[baseCx, seatY + 45], [baseCx, seatY + 71]], C.ink2, 7);
  ln(ctx, [[baseCx - 2, seatY + 47], [baseCx - 65, seatY + 48]], C.ink2, 6);
  ln(ctx, [[baseCx + 2, seatY + 47], [baseCx + 65, seatY + 48]], C.ink2, 6);
  for (const [dx, dy] of [
    [-65, 48],
    [-48, 65],
    [0, 71],
    [48, 65],
    [65, 48],
  ] as const) {
    el(ctx, baseCx + dx, seatY + dy, 7.5, 7.5, C.ink2, C.ink, 1);
    el(ctx, baseCx + dx - 2, seatY + dy - 2, 2, 2, "#7D8592");
  }
}

function drawChairFront(ctx: CanvasRenderingContext2D, cx: number, seatY: number) {
  rr(ctx, cx - 68, seatY - 120, 136, 126, 22, C.chair, C.chairEdge, 1.2);
  rr(ctx, cx - 58, seatY - 112, 116, 108, 17, "#E0E4EC");
  rr(ctx, cx - 80, seatY - 6, 160, 22, 10, C.seat, C.chairEdge, 1);
  rr(ctx, cx - 68, seatY - 5, 136, 9, 4, "#DDE2EA");
  rr(ctx, cx - 88, seatY - 26, 13, 36, 5, C.ink2);
  rr(ctx, cx + 75, seatY - 26, 13, 36, 5, C.ink2);
  rr(ctx, cx - 92, seatY - 30, 21, 9, 4, C.ink);
  rr(ctx, cx + 71, seatY - 30, 21, 9, 4, C.ink);
  rr(ctx, cx - 5, seatY + 12, 10, 34, 3, C.ink2);
  el(ctx, cx, seatY + 45, 10, 7, C.ink2);
  ln(ctx, [[cx, seatY + 45], [cx - 51, seatY + 65]], C.ink2, 7);
  ln(ctx, [[cx, seatY + 45], [cx + 51, seatY + 65]], C.ink2, 7);
  ln(ctx, [[cx, seatY + 45], [cx, seatY + 71]], C.ink2, 7);
  ln(ctx, [[cx - 2, seatY + 47], [cx - 70, seatY + 48]], C.ink2, 6);
  ln(ctx, [[cx + 2, seatY + 47], [cx + 70, seatY + 48]], C.ink2, 6);
  for (const [dx, dy] of [
    [-70, 48],
    [-51, 65],
    [0, 71],
    [51, 65],
    [70, 48],
  ] as const) {
    el(ctx, cx + dx, seatY + dy, 7.5, 7.5, C.ink2, C.ink, 1);
    el(ctx, cx + dx - 2, seatY + dy - 2, 2, 2, "#7D8592");
  }
}

function drawDeskSurface(ctx: CanvasRenderingContext2D, cx: number, top: number, w: number) {
  const x = cx - w / 2;
  poly(ctx, [[x + 25, top + 10], [x + 15, top + 78], [x + 23, top + 78], [x + 37, top + 10]], "#B9C1CE");
  poly(
    ctx,
    [[x + w - 37, top + 10], [x + w - 23, top + 78], [x + w - 15, top + 78], [x + w - 25, top + 10]],
    "#B9C1CE",
  );
  rr(ctx, x, top, w, 14, 4, C.desk, C.deskEdge, 1);
  ln(ctx, [[x + 8, top + 3], [x + w - 8, top + 3]], "#fff", 2);
  rr(ctx, x + 2, top + 9, w - 4, 5, 2, "#E5E8EF");
}

/** Code-line patterns; `scroll` (chars / pace) shifts which lines are visible. */
const MONITOR_LINES: [number, number, boolean][] = [
  [0.53, 0.2, true],
  [0.32, 0.3, false],
  [0.68, 0.12, true],
  [0.45, 0.23, false],
  [0.58, 0.17, true],
  [0.4, 0.28, false],
  [0.62, 0.15, true],
  [0.35, 0.22, false],
  [0.7, 0.1, true],
  [0.48, 0.2, false],
  [0.55, 0.18, true],
  [0.38, 0.25, false],
];

function drawMonitor(
  ctx: CanvasRenderingContext2D,
  x: number,
  deskTop: number,
  w = 112,
  h = 87,
  scroll = 0,
) {
  rr(ctx, x, deskTop - h, w, h, 7, C.ink, C.ink2, 1);
  rr(ctx, x + 5, deskTop - h + 5, w - 10, h - 10, 4, C.screen);
  for (let i = 0; i < 3; i++) el(ctx, x + w - 17 + i * 4, deskTop - h + 11, 1.3, 1.3, "#8792A2");
  const visible = 5;
  const start = Math.floor(Math.max(0, scroll)) % MONITOR_LINES.length;
  const pixelShift = (scroll % 1) * 9;
  for (let i = 0; i < visible + 1; i++) {
    const row = MONITOR_LINES[(start + i) % MONITOR_LINES.length];
    const yy = deskTop - h + 20 + i * 9 - pixelShift;
    if (yy < deskTop - h + 14 || yy > deskTop - 18) continue;
    rr(ctx, x + 10, yy, w * row[0] * 0.62, 3, 1.5, row[2] ? C.blue : "#A0A9B7");
    rr(ctx, x + 14 + w * row[0] * 0.62, yy, w * row[1], 3, 1.5, "#667283");
  }
  rr(ctx, x + w / 2 - 3, deskTop - 10, 6, 7, 2, C.ink2);
  rr(ctx, x + w / 2 - 17, deskTop - 4, 34, 4, 2, "#B0B8C4");
}

function drawLaptop(ctx: CanvasRenderingContext2D, x: number, deskTop: number, w = 105) {
  const h = 48;
  rr(ctx, x, deskTop - h, w, h, 5, "#D7DCE5", "#C3CAD5", 1);
  rr(ctx, x + 5, deskTop - h + 5, w - 10, h - 10, 3, "#E9ECF2");
  el(ctx, x + w / 2, deskTop - h / 2, 2.4, 2.4, "#C1C8D3");
  poly(
    ctx,
    [[x - 8, deskTop], [x + w + 8, deskTop], [x + w + 13, deskTop + 5], [x - 13, deskTop + 5]],
    "#C4CBD6",
    "#B8C0CD",
    1,
  );
  ln(ctx, [[x - 7, deskTop + 1], [x + w + 7, deskTop + 1]], "#F8F9FC", 1.5);
}

function drawKeyboard(ctx: CanvasRenderingContext2D, cx: number, top: number, phase: number) {
  rr(ctx, cx - 44, top - 17, 98, 13, 4, C.ink2, C.ink, 1);
  for (let r = 0; r < 2; r++) {
    for (let i = 0; i < 10; i++) {
      let keyColor = C.key;
      if (phase >= 0) {
        if (phase === 0 && r === 0 && i === 2) keyColor = C.blue;
        if (phase === 0 && r === 1 && i === 6) keyColor = C.blue;
        if (phase === 1 && r === 0 && i === 5) keyColor = C.blue;
        if (phase === 1 && r === 1 && i === 3) keyColor = C.blue;
        if (phase === 2 && r === 0 && i === 8) keyColor = C.blue;
        if (phase === 2 && r === 1 && i === 1) keyColor = C.blue;
      }
      rr(ctx, cx - 38 + i * 8.8, top - 13 + r * 4, 6.5, 2.5, 0.7, keyColor);
    }
  }
}

function drawWorkingBack(
  ctx: CanvasRenderingContext2D,
  cx: number,
  phase: number,
  scroll = 0,
  typing = false,
) {
  const top = 324;
  const w = 380;
  const seatY = 322;
  const bodyCx = cx + 4;
  // Seat under the figure (no tall backrest — it was covering the hands).
  drawChairSeatBackView(ctx, cx, seatY);
  drawSeatedLegsBack(ctx, bodyCx - 2, seatY - 14);
  drawMonitor(ctx, cx + 18, top, 112, 87, scroll);
  drawDeskSurface(ctx, cx, top, w);
  drawKeyboard(ctx, cx, top, typing ? phase : -1);
  drawTorso(ctx, bodyCx, top - 61);
  drawHeadBack(ctx, bodyCx, top - 111, -0.06);
  // Arms last so hands sit on top of the keyboard.
  drawTypingArms(ctx, bodyCx, top - 61, top, phase, typing);
  if (typing) {
    ln(ctx, [[cx + 132, top - 74], [cx + 138, top - 81]], C.blue, 2.4);
    ln(ctx, [[cx + 141, top - 65], [cx + 150, top - 68]], C.blue, 2.4);
    ln(ctx, [[cx + 136, top - 56], [cx + 143, top - 54]], C.blue, 2.4);
  }
}

function drawDoneFront(
  ctx: CanvasRenderingContext2D,
  cx: number,
  face: MascotFace,
  greet: number,
) {
  const top = 324;
  const w = 380;
  const seatY = 322;
  const bodyCx = cx - 5;
  drawLaptop(ctx, cx + 40, top, 102);
  drawDeskSurface(ctx, cx, top, w);
  drawChairFront(ctx, cx, seatY);
  drawTorso(ctx, bodyCx, top - 61);
  drawHeadFront(ctx, bodyCx, top - 111, face);
  drawRestingArm(ctx, bodyCx, top - 61);
  drawWaveArm(ctx, bodyCx, top - 61, greet);
  drawSeatedLegsFront(ctx, bodyCx, seatY);
  el(ctx, cx + 128, 168, 14, 14, C.bluePale);
  ln(ctx, [[cx + 121, 168], [cx + 126, 173], [cx + 135, 162]], C.blue, 2.4);
}

/** Paint into a 220×180 logical canvas (transparent background). */
export function paintAgentMascot(
  ctx: CanvasRenderingContext2D,
  opts: {
    working: boolean;
    face: MascotFace;
    turn: number;
    phase: number;
    greet: number;
    /** Continuous scroll driven by typing pace (chars streaming). */
    scroll?: number;
    /** True only while tokens are actually arriving. */
    typing?: boolean;
  },
) {
  const { face, turn, phase, greet, scroll = 0, typing = false } = opts;
  const showFront = turn >= 0.55;
  // Larger crop so the chair + robot stay readable on the board.
  const s = 0.42;
  ctx.save();
  ctx.translate(110, 94);
  ctx.scale(s, s);
  ctx.translate(-280, -260);
  try {
    if (showFront) {
      drawDoneFront(ctx, 280, face === "work" ? "happy" : face, greet);
    } else {
      drawWorkingBack(ctx, 280, phase, scroll, typing);
    }
  } catch {
    /* keep canvas blank rather than breaking the frame loop */
  }
  ctx.restore();
}
