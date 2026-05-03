// FIXTURE: link_no_text_001 — CORRECT VERSION
// WCAG: 2.4.4 — Link Purpose (In Context)
// Fix: added aria-label to icon-only links so purpose is conveyed to screen readers

import React from "react";

// FIX 1: aria-label="Twitter profile" added to SVG-only link
// FIX 2: aria-label="GitHub profile" added to empty-text link
// FIX 3: aria-label="LinkedIn profile" added to whitespace-only link
const SocialLinks: React.FC = () => {
  return (
    <nav className="social-links" aria-label="Social media links">
      <a
        href="https://twitter.com/example"
        className="icon-twitter"
        aria-label="Twitter profile"
      >
        <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
          <path d="M23 3a10.9 10.9 0 01-3.14 1.53..." />
        </svg>
      </a>

      <a
        href="https://github.com/example"
        className="icon-github"
        aria-label="GitHub profile"
      >
        <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
          <path d="M12 0C5.37..." />
        </svg>
      </a>

      <a
        href="https://linkedin.com/in/example"
        className="icon-linkedin"
        aria-label="LinkedIn profile"
      >
        <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
          <path d="M16 8a6 6 0 016 6v7h-4v-7..." />
        </svg>
      </a>
    </nav>
  );
};

export default SocialLinks;
