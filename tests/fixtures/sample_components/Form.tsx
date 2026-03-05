/**
 * Form.tsx — Formulário com múltiplos issues de acessibilidade para testes.
 *
 * Issues presentes:
 * - Input sem label associado
 * - Imagem sem alt text
 * - Div usada como botão sem role nem tabIndex
 * - required sem aria-required
 */

import React, { useState } from 'react';

const LoginForm: React.FC = () => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !password) {
      setError('Please fill all fields');
    }
  };

  return (
    <form onSubmit={handleSubmit}>
      <img src="/logo.png" /> {/* Sem alt text! */}

      {/* Input sem label */}
      <input
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="Email"
      />

      <input
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        placeholder="Password"
      />

      {error && <p>{error}</p>}

      {/* div como botão sem acessibilidade */}
      <div onClick={handleSubmit} style={{ cursor: 'pointer', background: '#007bff', color: '#fff', padding: '10px' }}>
        Login
      </div>
    </form>
  );
};

export default LoginForm;
