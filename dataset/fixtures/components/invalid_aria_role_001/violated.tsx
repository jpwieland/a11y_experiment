// FIXTURE: invalid_aria_role_001
// WCAG: 4.1.2 — Name, Role, Value
// Violation: div elements with invalid/non-existent ARIA roles
// Component: NavigationMenu

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

// VIOLATION 1: role="navigation-menu" is not a valid ARIA role
// VIOLATION 2: role="nav-item" is not a valid ARIA role
const NavigationMenu: React.FC = () => {
  return (
    <div role="navigation-menu" className="nav-container">
      <ul>
        {navItems.map((item) => (
          <li key={item.href} role="nav-item">
            <a href={item.href}>{item.label}</a>
          </li>
        ))}
      </ul>
    </div>
  );
};

export default NavigationMenu;
