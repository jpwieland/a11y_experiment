// FIXTURE: tabindex_positive_001 — CORRECT VERSION
// WCAG: 2.4.3 — Focus Order
// Fix: removed positive tabIndex values so natural DOM focus order is preserved

import React, { useState } from "react";

// FIX 1: removed tabIndex={3} from name field
// FIX 2: removed tabIndex={1} from email field
// FIX 3: removed tabIndex={2} from message textarea
// Natural DOM order (name → email → message → submit) is now the focus order
const ContactForm: React.FC = () => {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
  };

  return (
    <form onSubmit={handleSubmit} className="contact-form">
      <div className="form-group">
        <label htmlFor="name">Full Name</label>
        <input
          id="name"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </div>

      <div className="form-group">
        <label htmlFor="email">Email Address</label>
        <input
          id="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
      </div>

      <div className="form-group">
        <label htmlFor="message">Message</label>
        <textarea
          id="message"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
        />
      </div>

      <button type="submit">Send Message</button>
    </form>
  );
};

export default ContactForm;
