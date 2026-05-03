// FIXTURE: focus_not_visible_001
// WCAG: 2.4.7 — Focus Visible
// Violation: CSS outline removed via inline style, making focus indicator invisible
// Component: CustomButton

import React from "react";

interface CustomButtonProps {
  label: string;
  onClick: () => void;
  variant?: "primary" | "secondary";
}

// VIOLATION 1: outline: "none" removes focus ring on primary button
// VIOLATION 2: outline: 0 removes focus ring on secondary button
const CustomButton: React.FC<CustomButtonProps> = ({
  label,
  onClick,
  variant = "primary",
}) => {
  if (variant === "secondary") {
    return (
      <button
        onClick={onClick}
        style={{
          backgroundColor: "#fff",
          color: "#333",
          border: "1px solid #ccc",
          outline: 0,
          padding: "8px 16px",
          cursor: "pointer",
        }}
      >
        {label}
      </button>
    );
  }

  return (
    <button
      onClick={onClick}
      style={{
        backgroundColor: "#0070f3",
        color: "#fff",
        border: "none",
        outline: "none",
        padding: "10px 20px",
        cursor: "pointer",
        borderRadius: "4px",
      }}
    >
      {label}
    </button>
  );
};

export default CustomButton;
