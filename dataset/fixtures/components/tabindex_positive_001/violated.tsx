// FIXTURE: tabindex_positive_001
// WCAG: 2.4.3 — Focus Order
// Violation: positive tabindex values override natural focus order
// Component: ContactForm

import React, { useState } from "react";

// VIOLATION 1: tabIndex={3} on name field — breaks natural tab order
// VIOLATION 2: tabIndex={1} on email field — jumps to top of focus order
// VIOLATION 3: tabIndex={2} on message field — unnatural order
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
          tabIndex={3}
        />
      </div>

      <div className="form-group">
        <label htmlFor="email">Email Address</label>
        <input
          id="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          tabIndex={1}
        />
      </div>

      <div className="form-group">
        <label htmlFor="message">Message</label>
        <textarea
          id="message"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          tabIndex={2}
        />
      </div>

      <button type="submit">Send Message</button>
    </form>
  );
};

export default ContactForm;
