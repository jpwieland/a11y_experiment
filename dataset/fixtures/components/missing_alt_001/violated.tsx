// fixture: missing_alt_001
// VIOLAÇÕES INJETADAS:
//   [V1] WCAG 1.1.1 — img sem atributo alt (linha 12)
//   [V2] WCAG 4.1.2 — button com apenas ícone, sem nome acessível (linha 17)
//
// NÃO edite este arquivo manualmente — é parte do corpus de avaliação.

import React from "react";

interface ProductCardProps {
  src: string;
  name: string;
  price: number;
  onBuy: () => void;
}

export function ProductCard({ src, name, price, onBuy }: ProductCardProps) {
  return (
    <div className="product-card">
      <img src={src} />
      <div className="product-info">
        <h3>{name}</h3>
        <span className="price">R$ {price.toFixed(2)}</span>
      </div>
      <button onClick={onBuy}>
        <svg aria-hidden="true" viewBox="0 0 24 24">
          <path d="M7 18c-1.1 0-1.99.9-1.99 2S5.9 22 7 22s2-.9 2-2-.9-2-2-2z" />
        </svg>
      </button>
    </div>
  );
}
