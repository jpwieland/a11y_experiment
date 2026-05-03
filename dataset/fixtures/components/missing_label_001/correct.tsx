// fixture: missing_label_001 — VERSÃO CORRETA (gold standard)

import React, { useState } from "react";

export function SearchForm() {
  const [query, setQuery] = useState("");
  const [email, setEmail] = useState("");
  const [category, setCategory] = useState("all");

  return (
    <form className="search-form">
      <label htmlFor="search-query">Buscar</label>
      <input
        id="search-query"
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Buscar..."
      />
      <label htmlFor="email-input">E-mail</label>
      <input
        id="email-input"
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="seu@email.com"
      />
      <label htmlFor="category-select">Categoria</label>
      <select
        id="category-select"
        value={category}
        onChange={(e) => setCategory(e.target.value)}
      >
        <option value="all">Todos</option>
        <option value="books">Livros</option>
        <option value="tech">Tecnologia</option>
      </select>
      <button type="submit">Buscar</button>
    </form>
  );
}
