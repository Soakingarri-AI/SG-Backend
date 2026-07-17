import type { Metadata } from "next";
import { Bullets, LegalPage, Section } from "@/components/LegalPage";
import { COMPANY } from "@/lib/company";

/**
 * ⚠️ TEMPLATE — REQUIRES LEGAL REVIEW BEFORE PUBLICATION.
 * This describes the data practices the codebase actually implements today, but
 * it is not legal advice and has not been reviewed by a qualified practitioner.
 * Have counsel review it against the NDPA 2023 (and GDPR if you serve EU users)
 * and fill in every placeholder in src/lib/company.ts first.
 */
export const metadata: Metadata = {
  title: "Privacy Policy",
  description:
    "How SoakinGarri collects, uses, stores, and protects your personal data.",
};

export default function PrivacyPage() {
  return (
    <LegalPage
      title="Privacy Policy"
      intro={`This policy explains what ${COMPANY.name} collects when you use our products, why we collect it, how long we keep it, and the control you have over it.`}
    >
      <Section n={1} title="Who we are">
        <p>
          {COMPANY.legalName} (&quot;{COMPANY.name}&quot;, &quot;we&quot;,
          &quot;us&quot;) operates {COMPANY.domain} and its associated products.
          We are the data controller for the personal data described in this
          policy. Our registered office is {COMPANY.address}.
        </p>
        <p>
          For any privacy question or request, contact{" "}
          <a href={`mailto:${COMPANY.email.privacy}`} className="text-brand-300 hover:underline">
            {COMPANY.email.privacy}
          </a>
          .
        </p>
      </Section>

      <Section n={2} title="What we collect">
        <p>We collect only what the products need to work:</p>
        <Bullets
          items={[
            "Account data — your email address, a password stored only as a one-way hash, and an optional display name.",
            "Content you submit — the questions, prompts, exam answers, and wizard inputs you send to our products.",
            "Generated output — the responses our systems produce for you, stored so you can return to your history.",
            "Technical data — IP address and request metadata, used for security, abuse prevention, and rate limiting.",
            "Uploaded files — any media or documents you choose to upload to a product.",
          ]}
        />
        <p>
          We do not collect payment card details directly. We do not sell your
          personal data, and we do not use it to build advertising profiles.
        </p>
      </Section>

      <Section n={3} title="Why we use it">
        <Bullets
          items={[
            "To provide the product you asked for — answering your query, generating your exam, producing your plan.",
            "To keep your history available across our products under a single account.",
            "To secure the service — detecting abuse, enforcing rate limits, and investigating incidents.",
            "To improve quality — diagnosing failures and measuring whether our output is accurate and useful.",
            "To communicate with you when you contact us, or about material changes to the service.",
          ]}
        />
        <p>
          Our lawful bases are the performance of our contract with you (to
          deliver the service), our legitimate interests (security and product
          improvement), your consent where we ask for it, and compliance with
          legal obligations.
        </p>
      </Section>

      <Section n={4} title="AI processing">
        <p>
          Our products use large language models to generate responses. Content
          you submit is sent to our AI processing provider solely to generate
          your response. We select providers that do not train their models on
          our customers&apos; data by default. AI output can be inaccurate — see
          our{" "}
          <a href="/terms" className="text-brand-300 hover:underline">
            Terms &amp; Conditions
          </a>{" "}
          for the limits of what we warrant.
        </p>
      </Section>

      <Section n={5} title="Sharing">
        <p>
          We share personal data only with service providers who process it on
          our behalf under contract — cloud hosting and database infrastructure,
          AI model providers, and error-monitoring tools. We may also disclose
          data where legally required, or to protect our rights, users, or the
          security of the service.
        </p>
      </Section>

      <Section n={6} title="International transfers">
        <p>
          Our infrastructure and processing providers may store or process data
          outside your country of residence. Where that happens we rely on
          appropriate safeguards, including contractual protections with each
          provider.
        </p>
      </Section>

      <Section n={7} title="Retention">
        <p>
          We keep account data for as long as your account exists. Conversation
          and generated history is retained so you can access it, and is deleted
          when you delete it or close your account. Security and request logs are
          kept for a limited period appropriate to their purpose, then removed.
          If you close your account we delete or anonymise your personal data,
          except where we must retain it to meet a legal obligation.
        </p>
      </Section>

      <Section n={8} title="Your rights">
        <p>
          Subject to applicable law, you have the right to access the personal
          data we hold about you, to correct it, to delete it, to object to or
          restrict certain processing, to withdraw consent where processing rests
          on consent, and to request a portable copy.
        </p>
        <p>
          Exercise any of these by emailing{" "}
          <a href={`mailto:${COMPANY.email.privacy}`} className="text-brand-300 hover:underline">
            {COMPANY.email.privacy}
          </a>
          . We respond within the period required by applicable law. You also
          have the right to complain to your data protection authority.
        </p>
      </Section>

      <Section n={9} title="Security">
        <p>
          We protect data in transit with TLS, store passwords only as salted
          one-way hashes, restrict internal access to what each role needs, and
          keep credentials in a managed secrets store rather than in our code. No
          service can promise perfect security, but we treat any incident
          affecting your data as a priority and will notify you and the relevant
          authority where the law requires it.
        </p>
      </Section>

      <Section n={10} title="Children">
        <p>
          Our education products are used by secondary-school students. Where a
          user is below the age of consent in their jurisdiction, they should use
          our products with the involvement of a parent, guardian, or school. If
          you believe a child has given us personal data without the appropriate
          consent, contact{" "}
          <a href={`mailto:${COMPANY.email.privacy}`} className="text-brand-300 hover:underline">
            {COMPANY.email.privacy}
          </a>{" "}
          and we will remove it.
        </p>
      </Section>

      <Section n={11} title="Cookies">
        <p>
          We use a small number of strictly necessary cookies to keep you signed
          in across our products and to protect against abuse. We do not use
          advertising or cross-site tracking cookies.
        </p>
      </Section>

      <Section n={12} title="Changes to this policy">
        <p>
          We update this policy as the service changes. The revision date appears
          at the top of this page, and we will tell you about material changes
          before they take effect.
        </p>
      </Section>
    </LegalPage>
  );
}
