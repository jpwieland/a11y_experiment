// FIXTURE: heading_hierarchy_001
// WCAG: 1.3.1 — Info and Relationships; 2.4.6 — Headings and Labels
// Violation: heading levels skipped, breaking logical document outline
// Component: ArticleCard

import React from "react";

interface ArticleCardProps {
  title: string;
  category: string;
  excerpt: string;
  author: string;
}

// VIOLATION 1: jumps from h2 (section title implied) to h4, skipping h3
// VIOLATION 2: uses h6 for author byline which breaks heading hierarchy
const ArticleCard: React.FC<ArticleCardProps> = ({
  title,
  category,
  excerpt,
  author,
}) => {
  return (
    <article className="article-card">
      <h2 className="article-title">{title}</h2>
      {/* VIOLATION: should be h3, not h4 — skips a level */}
      <h4 className="article-category">{category}</h4>
      <p className="article-excerpt">{excerpt}</p>
      {/* VIOLATION: should be a <p> or <span>, not h6 — misuses heading semantics */}
      <h6 className="article-author">By {author}</h6>
    </article>
  );
};

export default ArticleCard;
