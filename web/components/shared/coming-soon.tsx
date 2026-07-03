import { Construction } from "lucide-react";
import { PageContainer } from "@/components/layout/page-container";
import { EmptyState } from "@/components/shared/states";

/** Temporary placeholder for routes being built out incrementally. */
export function ComingSoon({ title, description }: { title: string; description?: string }) {
  return (
    <PageContainer>
      <EmptyState
        icon={Construction}
        title={title}
        description={description ?? "This page is being built out. Check back shortly."}
        className="mt-8"
      />
    </PageContainer>
  );
}
