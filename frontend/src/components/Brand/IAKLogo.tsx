import React from 'react';

interface IAKLogoProps {
  stage?: number;
  reducedMotion?: boolean;
  className?: string;
}

export const IAKLogo: React.FC<IAKLogoProps> = ({ 
  stage = 9, 
  reducedMotion = false,
  className = "" 
}) => {
  const getTransition = () => reducedMotion ? 'none' : 'opacity 0.7s ease-out';

  return (
    <svg 
      viewBox="0 0 400 400" 
      xmlns="http://www.w3.org/2000/svg"
      className={`w-full h-full select-none ${className}`}
      aria-label="IAK Brand Sentinel Logo"
    >
      <defs>
        {/* Background Glow */}
        <radialGradient id="bgGlow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#F8B500" stopOpacity="0.15" />
          <stop offset="100%" stopColor="#0b0b13" stopOpacity="0" />
        </radialGradient>

        {/* Deep Gold Gradient for the I */}
        <linearGradient id="goldGradient" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#FCEABB" />
          <stop offset="25%" stopColor="#F8B500" />
          <stop offset="50%" stopColor="#B27900" />
          <stop offset="75%" stopColor="#F8B500" />
          <stop offset="100%" stopColor="#FCEABB" />
        </linearGradient>

        {/* Bright Silver Gradient for Crown and A/K */}
        <linearGradient id="silverGradient" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#FFFFFF" />
          <stop offset="20%" stopColor="#E0E5EC" />
          <stop offset="50%" stopColor="#9BA3AC" />
          <stop offset="80%" stopColor="#E0E5EC" />
          <stop offset="100%" stopColor="#FFFFFF" />
        </linearGradient>

        {/* Filters for Glow Effect */}
        <filter id="glowGold" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="8" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
        <filter id="glowSilver" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="4" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
      </defs>

      {/* Dim Base Background Glow */}
      <rect width="100%" height="100%" fill="#08080f" opacity="0.3" rx="24" />
      <rect width="100%" height="100%" fill="url(#bgGlow)" rx="24" />

      <g transform="translate(20, 20)">
        
        {/* ================= 1. THE LETTER I (iak-body) — Active Stage >= 2 ================= */}
        <g 
          id="iak-body" 
          data-part="body"
          transform="translate(35, 75)" 
          filter="url(#glowGold)"
          style={{ 
            opacity: stage >= 2 ? 1 : 0, 
            transition: getTransition() 
          }}
        >
          <path d="
            M 10,0 L 50,0 Q 40,5 40,15 
            L 40,225 Q 40,235 50,240 
            L 10,240 Q 20,235 20,225 
            L 20,15 Q 20,5 10,0 Z
          " fill="url(#goldGradient)" />
        </g>

        {/* ================= 2. THE GOLD SWIRL (iak-connector) — Active Stage >= 3 ================= */}
        <g 
          id="iak-connector" 
          data-part="connector"
          transform="translate(0, 0)" 
          filter="url(#glowGold)"
          style={{ 
            opacity: stage >= 3 ? 1 : 0, 
            transition: getTransition() 
          }}
        >
          <path 
            d="M 70,300 C 60,350 140,360 170,300 C 200,240 220,130 180,120 C 140,110 120,160 140,190 C 160,220 220,170 300,170" 
            fill="none" 
            stroke="url(#goldGradient)" 
            strokeWidth="8" 
            strokeLinecap="round" 
          />
          <path 
            d="M 70,300 C 60,350 140,360 170,300 C 200,240 220,130 180,120 C 140,110 120,160 140,190 C 160,220 220,170 300,170" 
            fill="none" 
            stroke="#FFEA8C" 
            strokeWidth="2" 
            strokeLinecap="round" 
            opacity="0.8" 
          />
        </g>

        {/* ================= 3. THE LETTER A (iak-a) — Active Stage >= 4 ================= */}
        <g 
          id="iak-a" 
          data-part="a"
          transform="translate(90, -10)"
          style={{ 
            opacity: stage >= 4 ? 1 : 0, 
            transition: getTransition() 
          }}
        >
          <path d="M 80,30 L 10,320 M 80,30 L 150,320" fill="none" stroke="url(#silverGradient)" strokeWidth="4" />
          <path d="M 72,50 L 18,315 M 88,50 L 142,315" fill="none" stroke="url(#silverGradient)" strokeWidth="1" opacity="0.7" />
        </g>

        {/* ================= 4. THE LETTER K (iak-k / iak-k1 & iak-k2) — Active Stage >= 5 & 6 ================= */}
        <g 
          id="iak-k" 
          data-part="k"
          transform="translate(140, 160)"
        >
          {/* K vertical spine (Stage 5) */}
          <g 
            id="iak-k1" 
            data-part="k1"
            style={{ 
              opacity: stage >= 5 ? 1 : 0, 
              transition: getTransition() 
            }}
          >
            <path d="M 30,10 L 30,160" fill="none" stroke="url(#silverGradient)" strokeWidth="4" />
            <path d="M 26,10 L 26,160 M 34,10 L 34,160" fill="none" stroke="url(#silverGradient)" strokeWidth="1" opacity="0.7" />
            <path d="M 15,10 L 45,10 M 15,160 L 45,160" fill="none" stroke="url(#silverGradient)" strokeWidth="2" />
          </g>

          {/* K diagonal branches (Stage 6) */}
          <g 
            id="iak-k2" 
            data-part="k2"
            style={{ 
              opacity: stage >= 6 ? 1 : 0, 
              transition: getTransition() 
            }}
          >
            <path d="M 30,90 L 100,10" fill="none" stroke="url(#silverGradient)" strokeWidth="4" />
            <path d="M 30,82 L 95,8 M 30,98 L 105,12" fill="none" stroke="url(#silverGradient)" strokeWidth="1" opacity="0.7" />
            <path d="M 45,72 Q 90,130 140,160" fill="none" stroke="url(#silverGradient)" strokeWidth="4" />
            <path d="M 38,78 Q 85,138 138,168 M 52,66 Q 95,122 142,152" fill="none" stroke="url(#silverGradient)" strokeWidth="1" opacity="0.7" />
          </g>
        </g>

        {/* ================= 5. THE CROWN (iak-crown) — Gated on Confirmed Readiness (Stage >= 8) ================= */}
        <g 
          id="iak-crown" 
          data-part="crown"
          transform="translate(45, 10)" 
          filter={stage >= 9 ? "url(#glowGold)" : "url(#glowSilver)"}
          style={{ 
            opacity: stage >= 8 ? 1 : 0, 
            transition: getTransition(),
            filter: stage >= 9 ? 'drop-shadow(0 0 16px rgba(248,181,0,0.8))' : (stage >= 8 ? 'drop-shadow(0 0 8px rgba(255,255,255,0.6))' : 'none')
          }}
        >
          <path d="M 10,50 Q 25,48 40,50 L 40,54 Q 25,52 10,54 Z" fill="url(#silverGradient)" />
          <path d="M 10,48 L 5,20 L 15,35 L 25,10 L 35,35 L 45,20 L 40,48 Z" fill="none" stroke="url(#silverGradient)" strokeWidth="2.5" strokeLinejoin="round" />
          <circle cx="5" cy="18" r="2" fill="url(#silverGradient)" />
          <circle cx="25" cy="8" r="2.5" fill="url(#silverGradient)" />
          <circle cx="45" cy="18" r="2" fill="url(#silverGradient)" />
        </g>

      </g>
    </svg>
  );
};
