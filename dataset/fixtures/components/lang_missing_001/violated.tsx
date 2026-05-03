// FIXTURE: lang_missing_001
// WCAG: 3.1.1 — Language of Page
// Violation: html element (root layout) missing lang attribute
// Component: RootLayout

import React from "react";

interface RootLayoutProps {
  children: React.ReactNode;
  title?: string;
}

// VIOLATION: <html> element missing lang attribute — screen readers
// cannot determine the document language for correct pronunciation.
const RootLayout: React.FC<RootLayoutProps> = ({
  children,
  title = "My App",
}) => {
  return (
    <html>
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>{title}</title>
      </head>
      <body>
        <main>{children}</main>
      </body>
    </html>
  );
};

export default RootLayout;
