// FIXTURE: aria_expanded_missing_001 — CORRECT VERSION
// WCAG: 4.1.2 — Name, Role, Value
// Fix: added aria-expanded to toggle button and aria-hidden to panel

import React, { useState } from "react";

interface AccordionPanelProps {
  title: string;
  content: string;
}

// FIX 1: added aria-expanded={isOpen} to toggle button
// FIX 2: added aria-hidden={!isOpen} to content panel and id for association
const AccordionPanel: React.FC<AccordionPanelProps> = ({ title, content }) => {
  const [isOpen, setIsOpen] = useState(false);
  const panelId = `accordion-panel-${title.replace(/\s+/g, "-").toLowerCase()}`;

  return (
    <div className="accordion">
      <button
        className="accordion-toggle"
        onClick={() => setIsOpen(!isOpen)}
        aria-expanded={isOpen}
        aria-controls={panelId}
      >
        {title}
        <span className="accordion-icon" aria-hidden="true">
          {isOpen ? "▲" : "▼"}
        </span>
      </button>

      <div
        id={panelId}
        className="accordion-content"
        aria-hidden={!isOpen}
        hidden={!isOpen}
      >
        <p>{content}</p>
      </div>
    </div>
  );
};

export default AccordionPanel;
