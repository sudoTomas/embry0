import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import GeneralTab from "./settings/GeneralTab";
import ContextTab from "./settings/ContextTab";
import IntegrationsTab from "./settings/IntegrationsTab";

export function SettingsPage() {
  return (
    <div className="p-6 space-y-6 max-w-5xl">
      <h1 className="text-3xl font-bold">Settings</h1>
      <Tabs defaultValue="general">
        <TabsList>
          <TabsTrigger value="general">General</TabsTrigger>
          <TabsTrigger value="context">Context</TabsTrigger>
          <TabsTrigger value="integrations">Integrations</TabsTrigger>
        </TabsList>
        <TabsContent value="general"><GeneralTab /></TabsContent>
        <TabsContent value="context"><ContextTab /></TabsContent>
        <TabsContent value="integrations"><IntegrationsTab /></TabsContent>
      </Tabs>
    </div>
  );
}
