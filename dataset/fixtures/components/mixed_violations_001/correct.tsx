// fixture: mixed_violations_001 — VERSÃO CORRETA (gold standard)

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
      <img src={avatarUrl} alt={`Foto de perfil de ${username}`} className="avatar" />
      <div className="profile-info">
        <h2>{username}</h2>
        <span
          role="status"
          aria-live="polite"
          className={`status ${isOnline ? "online" : "offline"}`}
        >
          {isOnline ? "Online" : "Offline"}
        </span>
        <p className="followers">{followers} seguidores</p>
      </div>
      <button
        className="follow-btn"
        onClick={() => setIsFollowing(!isFollowing)}
        aria-label={isFollowing ? `Deixar de seguir ${username}` : `Seguir ${username}`}
        aria-pressed={isFollowing}
        type="button"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2z"/></svg>
      </button>
      <div className="bio-section">
        <label htmlFor="bio-input">Bio</label>
        <input
          id="bio-input"
          type="text"
          defaultValue={bio}
          placeholder="Sua bio..."
        />
      </div>
      <p>
        Veja mais informações{" "}
        <a href={profileUrl}>sobre o perfil de {username}</a>.
      </p>
    </section>
  );
}
