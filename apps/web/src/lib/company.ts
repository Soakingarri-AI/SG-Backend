/**
 * Single source of truth for company identity used across the marketing site
 * and legal pages.
 *
 * ⚠️ ACTION REQUIRED BEFORE LAUNCH
 * The values marked TODO are placeholders. Do not publish the legal pages until
 * a real registered entity name, address, and contact routes are filled in —
 * publishing a privacy policy or terms page that names a non-existent entity is
 * worse than having no page at all.
 */
export const COMPANY = {
  name: "SoakinGarri",
  productName: "SoakinGarri AI",
  domain: "soakingarri.com",

  // TODO: replace with the registered legal entity name once incorporated.
  legalName: "[Registered legal entity name]",
  // TODO: replace with the company registration / RC number.
  registrationNumber: "[Company registration number]",
  // TODO: replace with the registered business address.
  address: "[Registered business address]",
  // TODO: confirm the jurisdiction whose law governs the Terms.
  jurisdiction: "[Jurisdiction, e.g. Lagos, Nigeria]",

  email: {
    general: "hello@soakingarri.com",
    support: "support@soakingarri.com",
    privacy: "privacy@soakingarri.com",
    legal: "legal@soakingarri.com",
  },

  // TODO: replace with real handles, or remove the entries you don't have.
  social: {
    x: "https://x.com/soakingarri",
    linkedin: "https://www.linkedin.com/company/soakingarri",
  },

  /** Last review date for the Privacy Policy and Terms. Update when edited. */
  legalLastUpdated: "17 July 2026",
} as const;
