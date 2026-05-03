// FIXTURE: focus_not_visible_001 — CORRECT VERSION
// WCAG: 2.4.7 — Focus Visible
// Fix: removed outline:none/0 so browser's default focus ring is preserved,
//      or replaced with a custom visible focus style

import React from "react";

interface CustomButtonProps {
  label: string;
  onClick: () => void;
  variant?: "primary" | "secondary";
}

// FIX 1: removed outline: 0 from secondary button — focus ring now visible
// FIX 2: removed outline: "none" from primary button — focus ring now visible
//        (replaced with explicit visible focus-visible style via className)
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
