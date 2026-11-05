// Same drawing as docs/logo.svg and packaging/make_icon.py.
export default function Logo({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="100 100 824 824" className={className} aria-hidden="true">
      <rect x="100" y="100" width="824" height="824" rx="190" fill="#2f5bd3" />
      <polygon points="318,232 598,232 706,340 706,792 318,792" fill="#fff" />
      <polygon points="598,232 706,340 598,340" fill="#bfcdf5" />
      <rect x="356" y="492" width="420" height="78" rx="10" fill="#ffd249" />
      <rect x="378" y="420" width="220" height="34" rx="17" fill="#2f5bd3" />
      <rect x="378" y="514" width="268" height="34" rx="17" fill="#0a0a0a" />
      <rect x="378" y="612" width="250" height="34" rx="17" fill="#2f5bd3" />
      <rect x="378" y="690" width="160" height="34" rx="17" fill="#2f5bd3" />
    </svg>
  );
}
