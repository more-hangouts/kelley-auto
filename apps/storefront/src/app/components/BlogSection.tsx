import Image from "next/image";
import Link from "next/link";
import { getPosts } from "@/lib/api";
import type { Post } from "@/types/cms";
import { isMediaDoc } from "@/types/cms";

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function coverUrl(post: Post): string {
  if (isMediaDoc(post.coverImage) && post.coverImage.url) return post.coverImage.url;
  return "/images/blog-1.jpg";
}

export default async function BlogSection() {
  const posts = await getPosts({ publishedOnly: true, limit: 3 });

  if (posts.length === 0) return null;

  const featured = posts[0];
  const small = posts.slice(1, 3);

  return (
    <section className="bg-white px-5 md:px-10 lg:px-20 py-10 md:py-16 lg:py-20">
      {/* Header */}
      <div className="flex flex-col md:flex-row gap-4 md:gap-10 items-start md:items-center">
        <h2 className="flex-1 text-3xl md:text-4xl lg:text-5xl font-semibold leading-tight lg:leading-[60px] tracking-tight text-neutral-700">
          Blog &amp; Automotive Insights
        </h2>
        <p className="flex-1 text-base md:text-lg text-neutral-600">
          Read guides, tips, and the latest updates to help you choose the right
          car with confidence.
        </p>
      </div>

      <div className="mt-8 md:mt-14 flex flex-col lg:flex-row gap-6">
        {/* Featured post */}
        <Link
          href={`/blog/${featured.slug}`}
          className="group relative flex-1 h-[300px] md:h-[420px] lg:h-[520px] overflow-hidden rounded-2xl"
        >
          <Image
            src={coverUrl(featured)}
            alt={featured.title}
            fill
            className="object-cover group-hover:scale-105 transition-transform duration-300"
          />
          <div className="absolute inset-0 bg-black/20" />
          <div className="absolute bottom-6 left-6 right-6 md:bottom-8 md:left-8 md:right-8 text-white">
            <div className="flex items-center gap-3 text-xs md:text-sm">
              <span>{formatDate(featured.publishedAt)}</span>
              {featured.readTime && (
                <>
                  <span className="size-2 rounded-full bg-white/50" />
                  <span>{featured.readTime}</span>
                </>
              )}
            </div>
            <h3 className="mt-2 md:mt-3 text-xl md:text-2xl font-semibold leading-7 md:leading-8">
              {featured.title}
            </h3>
            {featured.excerpt && (
              <p className="mt-1 md:mt-2 text-base md:text-lg leading-6 md:leading-7 line-clamp-2 md:line-clamp-none">
                {featured.excerpt}
              </p>
            )}
            <div className="mt-2 md:mt-3 flex items-center gap-3 text-sm md:text-base font-medium text-primary">
              Read more
              <svg className="size-5" fill="none" viewBox="0 0 20 20">
                <path
                  d="M4.17 10h11.66M10 4.17 15.83 10 10 15.83"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
          </div>
        </Link>

        {/* Small posts */}
        {small.length > 0 && (
          <div className="flex flex-1 flex-col gap-6">
            {small.map((post) => (
              <Link
                key={post.id}
                href={`/blog/${post.slug}`}
                className="group flex flex-col sm:flex-row flex-1 gap-4 md:gap-6 overflow-hidden rounded-2xl shadow-[0_0_8px_rgba(0,0,0,0.08)]"
              >
                <div className="relative h-[200px] sm:h-auto sm:w-[200px] md:w-[260px] lg:w-[302px] shrink-0 overflow-hidden rounded-2xl">
                  <Image
                    src={coverUrl(post)}
                    alt={post.title}
                    fill
                    className="object-cover group-hover:scale-105 transition-transform duration-300"
                  />
                </div>
                <div className="flex flex-col justify-between p-4 sm:py-4 sm:pr-4 md:py-6 md:pr-6 sm:pl-0">
                  <div>
                    <div className="flex items-center gap-3 text-xs md:text-sm text-neutral-500">
                      <span>{formatDate(post.publishedAt)}</span>
                      {post.readTime && (
                        <>
                          <span className="size-2 rounded-full bg-neutral-200" />
                          <span>{post.readTime}</span>
                        </>
                      )}
                    </div>
                    <h3 className="mt-2 md:mt-3 text-lg md:text-2xl font-semibold text-neutral-700 leading-6 md:leading-8">
                      {post.title}
                    </h3>
                    {post.excerpt && (
                      <p className="mt-1 md:mt-2 text-sm md:text-lg text-neutral-500 leading-5 md:leading-7 line-clamp-3">
                        {post.excerpt}
                      </p>
                    )}
                  </div>
                  <div className="mt-3 md:mt-0 flex items-center gap-3 text-sm md:text-base font-medium text-primary">
                    Read more
                    <svg className="size-5" fill="none" viewBox="0 0 20 20">
                      <path
                        d="M4.17 10h11.66M10 4.17 15.83 10 10 15.83"
                        stroke="currentColor"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>

      <div className="mt-8 text-center">
        <Link
          href="/blog"
          className="inline-flex items-center gap-2 text-sm font-medium text-primary hover:text-primary-dark transition-colors"
        >
          View all posts
          <svg className="size-4" fill="none" viewBox="0 0 16 16">
            <path
              d="M3.33 8h9.34M8.67 4l4 4-4 4"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </Link>
      </div>
    </section>
  );
}
