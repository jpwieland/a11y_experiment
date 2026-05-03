// FIXTURE: link_no_text_001
// WCAG: 2.4.4 — Link Purpose (In Context)
// Violation: anchor tags with no accessible text (icon-only links without aria-label)
// Component: SocialLinks

import React from "react";

// VIOLATION 1: <a> with only an icon, no text and no aria-label
// VIOLATION 2: <a> with empty text content
// VIOLATION 3: <a> with only whitespace text
const SocialLinks: React.FC = () => {
  return (
    <nav className="social-links">
      <a href="https://twitter.com/example" className="icon-twitter">
        <svg viewBox="0 0 24 24" width="24" height="24">
          <path d="M23 3a10.9 10.9 0 01-3.14 1.53..." />
        </svg>
      </a>

      <a href="https://github.com/example" className="icon-github">
        {""}
      </a>

      <a href="https://linkedin.com/in/example" className="icon-linkedin">
        {" "}
      </a>
    </nav>
  );
};

export default SocialLinks;
