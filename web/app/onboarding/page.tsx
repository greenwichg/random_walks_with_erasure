import type { Metadata } from "next";
import { OnboardingFlow } from "@/components/onboarding/onboarding-flow";

export const metadata: Metadata = {
  title: "Get started",
  description:
    "A health check for your news diet — build your Initial Information Health Estimate in under two minutes, no account needed to start.",
};

/** Public onboarding entry: value → pick outlets → Initial Estimate → save (Google sign-in). */
export default function OnboardingPage() {
  return <OnboardingFlow />;
}
