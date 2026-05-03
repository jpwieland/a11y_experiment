// FIXTURE: heading_hierarchy_001 — CORRECT VERSION
// WCAG: 1.3.1 — Info and Relationships; 2.4.6 — Headings and Labels
// Fix: restored sequential heading hierarchy, replaced misused h6 with <p>

import React from "react";

interface ArticleCardProps {
  title: string;
  category: string;
  excerpt: string;
  author: string;
}

// FIX 1: h4 → h3 to maintain sequential heading levels (h2 → h3)
// FIX 2: h6 → <p> because author byline is not a section heading
const ArticleCard: React.FC<ArticleCardProps> = ({
  title,
  category,
  excerpt,
  author,
}) => {
  return (
    <article className="article-card">
      <h2 className="article-title">{title}</h2>
      <h3 className="article-category">{category}</h3>
      <p className="article-excerpt">{excerpt}</p>
      <p className="article-author">By {author}</p>
    </article>
  );
};

export default ArticleCard;
