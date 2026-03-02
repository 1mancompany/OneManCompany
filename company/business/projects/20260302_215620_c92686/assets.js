window.GameAssets = {
    bird: {
        red: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">
    <!-- Body -->
    <circle cx="50" cy="55" r="40" fill="#e53935"/>
    <path d="M 22 75 Q 50 100 78 75 Q 50 85 22 75" fill="#ffcdd2"/>
    <!-- Tail feathers -->
    <path d="M 12 55 L 0 45 L 8 60 L 2 70 L 15 65 Z" fill="#212121"/>
    <!-- Top feathers -->
    <path d="M 50 15 C 40 0, 60 -5, 60 10 C 70 0, 75 10, 60 15 Z" fill="#e53935"/>
    <!-- Eyes -->
    <circle cx="36" cy="45" r="9" fill="white" stroke="#c62828" stroke-width="1"/>
    <circle cx="64" cy="45" r="9" fill="white" stroke="#c62828" stroke-width="1"/>
    <circle cx="40" cy="45" r="3.5" fill="black"/>
    <circle cx="60" cy="45" r="3.5" fill="black"/>
    <!-- Angry Eyebrows -->
    <path d="M 20 35 L 46 43 L 46 36 L 20 28 Z" fill="#212121"/>
    <path d="M 80 35 L 54 43 L 54 36 L 80 28 Z" fill="#212121"/>
    <!-- Beak -->
    <path d="M 38 56 L 62 56 L 50 68 Z" fill="#fbc02d" stroke="#f57f17" stroke-width="1"/>
    <path d="M 42 68 L 58 68 L 50 76 Z" fill="#f57f17" stroke="#e65100" stroke-width="1"/>
</svg>`
    },
    blocks: {
        wood: {
            normal: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">
    <rect width="100" height="100" fill="#8d6e63" stroke="#5d4037" stroke-width="4"/>
    <line x1="0" y1="20" x2="100" y2="20" stroke="#795548" stroke-width="2"/>
    <line x1="0" y1="50" x2="100" y2="50" stroke="#795548" stroke-width="2"/>
    <line x1="0" y1="80" x2="100" y2="80" stroke="#795548" stroke-width="2"/>
    <circle cx="20" cy="35" r="3" fill="#5d4037" opacity="0.5"/>
    <circle cx="80" cy="65" r="4" fill="#5d4037" opacity="0.5"/>
</svg>`,
            lightDamage: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">
    <rect width="100" height="100" fill="#8d6e63" stroke="#5d4037" stroke-width="4"/>
    <line x1="0" y1="20" x2="100" y2="20" stroke="#795548" stroke-width="2"/>
    <line x1="0" y1="50" x2="100" y2="50" stroke="#795548" stroke-width="2"/>
    <line x1="0" y1="80" x2="100" y2="80" stroke="#795548" stroke-width="2"/>
    <!-- Cracks -->
    <path d="M 10 0 L 25 25 L 15 45 M 85 100 L 75 75 L 90 60" fill="none" stroke="#3e2723" stroke-width="2" stroke-linejoin="round"/>
</svg>`,
            heavyDamage: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">
    <rect width="100" height="100" fill="#8d6e63" stroke="#5d4037" stroke-width="4"/>
    <line x1="0" y1="20" x2="100" y2="20" stroke="#795548" stroke-width="2"/>
    <line x1="0" y1="50" x2="100" y2="50" stroke="#795548" stroke-width="2"/>
    <line x1="0" y1="80" x2="100" y2="80" stroke="#795548" stroke-width="2"/>
    <!-- Cracks -->
    <path d="M 10 0 L 25 25 L 15 45 L 35 65 L 20 100" fill="none" stroke="#3e2723" stroke-width="3" stroke-linejoin="round"/>
    <path d="M 85 100 L 75 75 L 90 60 L 65 40 L 80 0" fill="none" stroke="#3e2723" stroke-width="3" stroke-linejoin="round"/>
    <path d="M 0 30 L 25 25" fill="none" stroke="#3e2723" stroke-width="2"/>
    <path d="M 100 70 L 75 75" fill="none" stroke="#3e2723" stroke-width="2"/>
    <!-- Splinters -->
    <polygon points="0,0 15,0 0,15" fill="#5d4037"/>
    <polygon points="100,100 85,100 100,85" fill="#5d4037"/>
</svg>`
        },
        ice: {
            normal: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">
    <rect width="100" height="100" fill="#e1f5fe" stroke="#81d4fa" stroke-width="4" opacity="0.9"/>
    <polygon points="5,5 40,5 5,40" fill="#ffffff" opacity="0.7"/>
    <polygon points="95,95 60,95 95,60" fill="#b3e5fc" opacity="0.7"/>
</svg>`,
            lightDamage: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">
    <rect width="100" height="100" fill="#e1f5fe" stroke="#81d4fa" stroke-width="4" opacity="0.9"/>
    <polygon points="5,5 40,5 5,40" fill="#ffffff" opacity="0.7"/>
    <polygon points="95,95 60,95 95,60" fill="#b3e5fc" opacity="0.7"/>
    <!-- Cracks -->
    <path d="M 50 50 L 20 15 M 50 50 L 85 35 M 50 50 L 40 85" fill="none" stroke="#ffffff" stroke-width="2" opacity="0.9"/>
</svg>`,
            heavyDamage: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">
    <rect width="100" height="100" fill="#e1f5fe" stroke="#81d4fa" stroke-width="4" opacity="0.9"/>
    <polygon points="5,5 40,5 5,40" fill="#ffffff" opacity="0.7"/>
    <polygon points="95,95 60,95 95,60" fill="#b3e5fc" opacity="0.7"/>
    <!-- Shatter Cracks -->
    <path d="M 50 50 L 20 15 M 50 50 L 85 35 M 50 50 L 40 85 M 20 15 L 0 30 M 85 35 L 100 60 M 40 85 L 75 100 M 50 50 L 10 70 M 50 50 L 80 5" fill="none" stroke="#ffffff" stroke-width="3" opacity="0.9"/>
    <circle cx="50" cy="50" r="4" fill="#ffffff" opacity="0.9"/>
    <!-- Missing chunks -->
    <polygon points="0,0 20,0 0,20" fill="#ffffff" opacity="0.5"/>
</svg>`
        }
    }
};