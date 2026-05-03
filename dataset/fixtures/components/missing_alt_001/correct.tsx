// fixture: missing_alt_001 — VERSÃO CORRETA (gold standard)
// Correções aplicadas:
//   [V1] alt descritivo adicionado ao img
//   [V2] aria-label adicionado ao button

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
      <img src={src} alt={`Foto do produto ${name}`} />
      <div className="product-info">
        <h3>{name}</h3>
        <span className="price">R$ {price.toFixed(2)}</span>
      </div>
      <button onClick={onBuy} aria-label={`Adicionar ${name} ao carrinho`}>
        <svg aria-hidden="true" viewBox="0 0 24 24">
          <path d="M7 18c-1.1 0-1.99.9-1.99 2S5.9 22 7 22s2-.9 2-2-.9-2-2-2z" />
        </svg>
      </button>
    </div>
  );
}
