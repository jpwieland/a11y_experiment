// fixture: mixed_violations_001
// VIOLAÇÕES INJETADAS:
//   [V1] WCAG 1.1.1 — avatar img sem alt
//   [V2] WCAG 4.1.2 — button de follow sem nome acessível
//   [V3] WCAG 1.3.1 — input de bio sem label
//   [V4] WCAG 4.1.2 — status badge com role inválido
//   [V5] WCAG 2.4.4 — link "aqui" sem contexto descritivo

import React, { useState } from "react";

interface UserProfileProps {
  username: string;
  avatarUrl: string;
  bio: string;
  followers: number;
  isOnline: boolean;
  profileUrl: string;
}

export function UserProfile({
  username,
  avatarUrl,
  bio,
  followers,
  isOnline,
  profileUrl,
}: UserProfileProps) {
  const [isFollowing, setIsFollowing] = useState(false);

  return (
    <section className="user-profile">
      <img src={avatarUrl} className="avatar" />
      <div className="profile-info">
        <h2>{username}</h2>
        <span role="article" className={`status ${isOnline ? "online" : "offline"}`}>
          {isOnline ? "Online" : "Offline"}
        </span>
        <p className="followers">{followers} seguidores</p>
      </div>
      <button
        className="follow-btn"
        onClick={() => setIsFollowing(!isFollowing)}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2z"/></svg>
      </button>
      <div className="bio-section">
        <input
          type="text"
          defaultValue={bio}
          placeholder="Sua bio..."
        />
      </div>
      <p>Veja mais informações <a href={profileUrl}>aqui</a>.</p>
    </section>
  );
}
