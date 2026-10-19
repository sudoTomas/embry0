import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import GeneralTab from "./settings/GeneralTab";
import ContextTab from "./settings/ContextTab";
import IntegrationsTab from "./settings/IntegrationsTab";
import ApiKeysTab from "./settings/ApiKeysTab";

export function SettingsPage() {
  return (
    <div className="space-y-6 max-w-5xl">
      <h1 className="text-xl font-bold">Settings</h1>
      <Tabs defaultValue="general">
        <TabsList>
          <TabsTrigger value="general">General</TabsTrigger>
          <TabsTrigger value="context">Context</TabsTrigger>
          <TabsTrigger value="integrations">Integrations</TabsTrigger>
          <TabsTrigger value="apikeys">API keys</TabsTrigger>
        </TabsList>
        <TabsContent value="general"><GeneralTab /></TabsContent>
        <TabsContent value="context"><ContextTab /></TabsContent>
        <TabsContent value="integrations"><IntegrationsTab /></TabsContent>
        <TabsContent value="apikeys"><ApiKeysTab /></TabsContent>
      </Tabs>
    </div>
  );
}
