// FIXTURE: aria_expanded_missing_001
// WCAG: 4.1.2 — Name, Role, Value
// Violation: accordion toggle button missing aria-expanded state attribute
// Component: AccordionPanel

import React, { useState } from "react";

interface AccordionPanelProps {
  title: string;
  content: string;
}

// VIOLATION 1: toggle button has no aria-expanded attribute
// VIOLATION 2: panel div has no aria-hidden corresponding to expanded state
const AccordionPanel: React.FC<AccordionPanelProps> = ({ title, content }) => {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="accordion">
      {/* VIOLATION: missing aria-expanded={isOpen} */}
      <button
        className="accordion-toggle"
        onClick={() => setIsOpen(!isOpen)}
      >
        {title}
        <span className="accordion-icon">{isOpen ? "▲" : "▼"}</span>
      </button>

      {/* VIOLATION: missing aria-hidden={!isOpen} */}
      {isOpen && (
        <div className="accordion-content">
          <p>{content}</p>
        </div>
      )}
    </div>
  );
};

export default AccordionPanel;
