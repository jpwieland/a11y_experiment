// FIXTURE: lang_missing_001 — CORRECT VERSION
// WCAG: 3.1.1 — Language of Page
// Fix: added lang="pt-BR" (or appropriate locale) to the <html> element

import React from "react";

interface RootLayoutProps {
  children: React.ReactNode;
  title?: string;
  lang?: string;
}

// FIX: added lang attribute to <html> element.
// Defaults to "pt-BR" to match the experiment's target locale (Brazilian Portuguese).
// Accept as prop to allow flexibility in tests.
const RootLayout: React.FC<RootLayoutProps> = ({
  children,
  title = "My App",
  lang = "pt-BR",
}) => {
  return (
    <html lang={lang}>
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
