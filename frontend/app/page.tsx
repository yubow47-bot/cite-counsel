import { FileText, Link2, Search } from "lucide-react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { CitationTool } from "@/components/citation-tool"
import { FileExtractView } from "@/components/file-extract-view"
import { UrlExtractView } from "@/components/url-extract-view"

export default function Page() {
  return (
    <main className="min-h-svh bg-background">
      <div className="mx-auto w-full max-w-[720px] px-4 py-12 sm:py-16">
        <header className="border-b border-border pb-6">
          <p className="text-xs font-medium uppercase tracking-widest text-primary">
            McGill Citation Guide
          </p>
          <h1 className="mt-2 text-balance font-serif text-3xl font-semibold leading-tight text-foreground sm:text-4xl">
            McGill Citation Generator
          </h1>
          <p className="mt-3 text-pretty text-sm leading-relaxed text-muted-foreground">
            Generate properly formatted citations according to the Canadian Guide
            to Uniform Legal Citation (McGill). Enter a case name, statute, or
            decision to get started.
          </p>
        </header>

        <Tabs defaultValue="query" className="mt-8 gap-6">
          <TabsList className="w-full">
            <TabsTrigger value="query" className="gap-1.5">
              <Search className="size-4" aria-hidden="true" />
              Citation Search
            </TabsTrigger>
            <TabsTrigger value="file" className="gap-1.5">
              <FileText className="size-4" aria-hidden="true" />
              File Extraction
            </TabsTrigger>
            <TabsTrigger value="url" className="gap-1.5">
              <Link2 className="size-4" aria-hidden="true" />
              URL Extraction
            </TabsTrigger>
          </TabsList>
          <TabsContent value="query">
            <CitationTool />
          </TabsContent>
          <TabsContent value="file">
            <FileExtractView />
          </TabsContent>
          <TabsContent value="url">
            <UrlExtractView />
          </TabsContent>
        </Tabs>

        <footer className="mt-12 border-t border-border pt-6 text-xs leading-relaxed text-muted-foreground">
          Citations are provided for reference. Always verify against the
          official McGill Guide before submission. Your feedback helps
          improve accuracy.
        </footer>
      </div>
    </main>
  )
}
