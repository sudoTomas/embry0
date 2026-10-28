import { useSearchParams } from "react-router";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import { SETTINGS_TABS, settingsTab } from "@/lib/settingsTabs";
import GeneralTab from "./settings/GeneralTab";
import ContextTab from "./settings/ContextTab";
import IntegrationsTab from "./settings/IntegrationsTab";
import ApiKeysTab from "./settings/ApiKeysTab";

export function SettingsPage() {
  // The open tab lives in the URL so the breadcrumb line names it.
  const [params, setParams] = useSearchParams();
  const tab = settingsTab(params.get("tab")).value;
  const openTab = (value: string) =>
    setParams(value === SETTINGS_TABS[0].value ? {} : { tab: value }, { replace: true });

  return (
    <div className="space-y-6 max-w-5xl">
      <h1 className="title-page text-xl font-bold">Settings</h1>
      <Tabs defaultValue={SETTINGS_TABS[0].value} value={tab} onValueChange={openTab}>
        <TabsList>
          {SETTINGS_TABS.map((t) => (
            <TabsTrigger key={t.value} value={t.value}>
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="general"><GeneralTab /></TabsContent>
        <TabsContent value="context"><ContextTab /></TabsContent>
        <TabsContent value="integrations"><IntegrationsTab /></TabsContent>
        <TabsContent value="apikeys"><ApiKeysTab /></TabsContent>
      </Tabs>
    </div>
  );
}
