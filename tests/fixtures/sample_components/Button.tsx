/**
 * Button.tsx — Componente com issues de acessibilidade intencionais para testes.
 *
 * Issues presentes:
 * - Sem aria-label no botão de fechar
 * - Contraste insuficiente (branco sobre amarelo)
 * - onClick sem onKeyDown correspondente
 */

import React, { useState } from 'react';

interface ButtonProps {
  onClick: () => void;
  label: string;
  variant?: 'primary' | 'danger';
  disabled?: boolean;
}

const Button: React.FC<ButtonProps> = ({ onClick, label, variant = 'primary', disabled = false }) => {
  const [isActive, setIsActive] = useState(false);

  const styles = {
    backgroundColor: variant === 'primary' ? '#ffdd00' : '#ff0000',
    color: variant === 'primary' ? '#ffffff' : '#ffffff', // Contraste insuficiente!
    padding: '8px 16px',
    border: 'none',
    borderRadius: '4px',
    cursor: disabled ? 'not-allowed' : 'pointer',
    opacity: disabled ? 0.5 : 1,
  };

  return (
    <div>
      <button
        style={styles}
        onClick={() => { setIsActive(!isActive); onClick(); }}
        disabled={disabled}
      >
        {label}
      </button>
      {/* Botão de fechar sem aria-label */}
      <button onClick={onClick}>×</button>
    </div>
  );
};

export default Button;
