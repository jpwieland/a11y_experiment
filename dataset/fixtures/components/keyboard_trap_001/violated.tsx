// fixture: keyboard_trap_001
// VIOLAÇÕES INJETADAS:
//   [V1] WCAG 2.1.1 — div clicável sem suporte a teclado (linha 18)
//   [V2] WCAG 2.1.1 — span interativo sem role e sem teclado (linha 26)

import React from "react";

interface ClickableCardProps {
  title: string;
  description: string;
  onSelect: () => void;
  onExpand: () => void;
}

export function ClickableCard({
  title,
  description,
  onSelect,
  onExpand,
}: ClickableCardProps) {
  return (
    <div className="card-container">
      <div className="card" onClick={onSelect}>
        <h3>{title}</h3>
        <p>{description}</p>
      </div>
      <span
        className="expand-button"
        onClick={onExpand}
        style={{ cursor: "pointer" }}
      >
        Ver mais
      </span>
    </div>
  );
}
