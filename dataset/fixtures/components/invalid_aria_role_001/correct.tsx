// FIXTURE: invalid_aria_role_001 — CORRECT VERSION
// WCAG: 4.1.2 — Name, Role, Value
// Fix: replaced invalid ARIA roles with valid semantic HTML and valid roles

import React from "react";

interface NavItem {
  label: string;
  href: string;
}

const navItems: NavItem[] = [
  { label: "Home", href: "/" },
  { label: "About", href: "/about" },
  { label: "Contact", href: "/contact" },
];

// FIX 1: replaced role="navigation-menu" with valid role="navigation" (or use <nav>)
// FIX 2: removed invalid role="nav-item" from <li> (listitem is the implicit role of <li>)
const NavigationMenu: React.FC = () => {
  return (
    <nav className="nav-container">
      <ul>
        {navItems.map((item) => (
          <li key={item.href}>
            <a href={item.href}>{item.label}</a>
          </li>
        ))}
      </ul>
    </nav>
  );
};

export default NavigationMenu;
