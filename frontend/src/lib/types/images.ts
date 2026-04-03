export interface ImageConfig {
  repo: string;
  dockerfile_path: string | null;
  dockerfile_content: string | null;
  image_override: string | null;
  platform: string;
  last_build_tag: string | null;
  last_build_status: "building" | "success" | "failed" | null;
  last_build_at: string | null;
  last_build_error: string | null;
}

export interface BuildStatus {
  status: "building" | "success" | "failed";
  tag: string | null;
  log_lines: string[];
  error: string | null;
}

export interface GenerateDockerfileResponse {
  dockerfile: string;
  detected_languages: string[];
}
