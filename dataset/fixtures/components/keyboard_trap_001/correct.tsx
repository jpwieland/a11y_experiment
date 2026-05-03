// fixture: keyboard_trap_001 — VERSÃO CORRETA (gold standard)

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
      <div
        className="card"
        onClick={onSelect}
        onKeyDown={(e) => e.key === "Enter" && onSelect()}
        role="button"
        tabIndex={0}
        aria-label={`Selecionar card: ${title}`}
      >
        <h3>{title}</h3>
        <p>{description}</p>
      </div>
      <button
        className="expand-button"
        onClick={onExpand}
        type="button"
      >
        Ver mais
      </button>
    </div>
  );
}
