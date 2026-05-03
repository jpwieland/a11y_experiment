// fixture: missing_label_001
// VIOLAÇÕES INJETADAS:
//   [V1] WCAG 1.3.1 / 4.1.2 — input search sem label (linha 10)
//   [V2] WCAG 4.1.2 — input email sem label associado (linha 14)
//   [V3] WCAG 4.1.2 — select sem label (linha 18)

import React, { useState } from "react";

export function SearchForm() {
  const [query, setQuery] = useState("");
  const [email, setEmail] = useState("");
  const [category, setCategory] = useState("all");

  return (
    <form className="search-form">
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Buscar..."
      />
      <input
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="seu@email.com"
      />
      <select value={category} onChange={(e) => setCategory(e.target.value)}>
        <option value="all">Todos</option>
        <option value="books">Livros</option>
        <option value="tech">Tecnologia</option>
      </select>
      <button type="submit">Buscar</button>
    </form>
  );
}
